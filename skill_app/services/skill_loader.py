from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from skill_app.domain.errors import ConfigurationError
from skill_app.schemas.skill import (
    LoadedSkill,
    LoadedSkillPackage,
    SkillInfo,
    SkillManifest,
    SkillPackageInfo,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = PROJECT_ROOT / "skill_app" / "skills"


def parse_skill(skill_md: Path) -> LoadedSkill:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"Missing YAML frontmatter: {skill_md}")

    try:
        _, frontmatter_text, body = text.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {skill_md}") from exc

    frontmatter = yaml.safe_load(frontmatter_text) or {}
    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    if not name or not description:
        raise ValueError(f"Skill must define name and description: {skill_md}")

    return LoadedSkill(
        name=name,
        description=description,
        path=str(skill_md.parent),
        body=body.strip(),
    )


def discover_skills(skills_dir: Path = DEFAULT_SKILLS_DIR) -> list[SkillInfo]:
    if not skills_dir.exists():
        return []

    seen: set[str] = set()
    skills: list[SkillInfo] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        skill = parse_skill(skill_md)
        if skill.name in seen:
            raise ValueError(f"Duplicate skill name: {skill.name}")
        seen.add(skill.name)
        skills.append(SkillInfo(name=skill.name, description=skill.description, path=skill.path))
    return skills


def load_skill(name: str, skills_dir: Path = DEFAULT_SKILLS_DIR) -> LoadedSkill:
    skill_md = skills_dir / name / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"Skill not found: {name}")
    return parse_skill(skill_md)


def load_references(skill: LoadedSkill, references: list[str]) -> dict[str, str]:
    references_dir = Path(skill.path) / "references"
    if not references_dir.exists():
        return {}

    loaded: dict[str, str] = {}
    for reference in references:
        ref_path = references_dir / reference
        if ref_path.suffix == "":
            ref_path = ref_path.with_suffix(".md")
        if not ref_path.exists():
            raise FileNotFoundError(f"Reference not found: {ref_path}")
        loaded[ref_path.name] = ref_path.read_text(encoding="utf-8")
    return loaded


def load_skill_package(
    name: str,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    *,
    validate_bindings: bool = True,
) -> LoadedSkillPackage:
    skill = load_skill(name, skills_dir)
    manifest_path = Path(skill.path) / "skill.yaml"
    if not manifest_path.is_file():
        raise ConfigurationError(
            f"Skill package manifest does not exist: {name}",
            {"skill_name": name, "path": str(manifest_path)},
        )
    try:
        manifest = SkillManifest.model_validate(
            yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        )
    except Exception as exc:
        raise ConfigurationError(
            f"Skill package manifest is invalid: {name}",
            {"skill_name": name, "path": str(manifest_path)},
        ) from exc
    if manifest.name != skill.name or Path(skill.path).name != skill.name:
        raise ConfigurationError(
            "Skill folder, SKILL.md, and skill.yaml names must match.",
            {
                "folder": Path(skill.path).name,
                "skill_md_name": skill.name,
                "manifest_name": manifest.name,
            },
        )
    instruction_path = Path(skill.path) / manifest.model.instruction
    if instruction_path.resolve() != (Path(skill.path) / "SKILL.md").resolve():
        raise ConfigurationError(
            "Skill model instruction must reference SKILL.md.",
            {"skill_name": name, "instruction": manifest.model.instruction},
        )
    if validate_bindings:
        validate_skill_bindings(manifest)
    instruction_bytes = instruction_path.read_bytes()
    component_instructions: dict[str, LoadedSkill] = {}
    component_hashes: dict[str, str] = {}
    for component_name in manifest.components:
        if component_name == manifest.name:
            raise ConfigurationError(
                "Skill package cannot include itself as a component.",
                {"skill_name": manifest.name},
            )
        component = load_skill(component_name, skills_dir)
        component_instructions[component_name] = component
        component_hashes[component_name] = hashlib.sha256(
            (Path(component.path) / "SKILL.md").read_bytes()
        ).hexdigest()
    return LoadedSkillPackage(
        manifest=manifest,
        instructions=skill,
        instruction_sha256=hashlib.sha256(instruction_bytes).hexdigest(),
        component_instructions=component_instructions,
        component_instruction_sha256s=component_hashes,
        package_sha256=_package_sha256(
            Path(skill.path),
            [
                Path(component.path)
                for component in component_instructions.values()
            ],
        ),
    )


def validate_skill_bindings(manifest: SkillManifest) -> None:
    from skill_app.services.skill_registry import (
        agent_registry,
        register_builtin_skill_bindings,
        schema_registry,
        validator_registry,
    )
    from skill_app.tools.builtin import register_builtin_tools
    from skill_app.tools.registry import tool_registry
    from skill_app.workflows.builtin import register_builtin_workflows
    from skill_app.workflows.registry import workflow_registry

    register_builtin_skill_bindings()
    register_builtin_tools()
    register_builtin_workflows()
    schema_registry.get(manifest.model.output_schema)
    for stage in manifest.stages.values():
        schema_registry.get(stage.output_schema)
    for agent in manifest.agents.values():
        agent_registry.get(agent)
    available_tools = {item.name for item in tool_registry.list_definitions()}
    missing_tools = sorted(set(manifest.tools.allow) - available_tools)
    if missing_tools:
        raise ConfigurationError(
            "Skill references unregistered tools.",
            {"skill_name": manifest.name, "tools": missing_tools},
        )
    for validator in [
        *manifest.validators.pre,
        *manifest.validators.output,
        *manifest.validators.post,
    ]:
        validator_registry.get(validator)
    if manifest.workflow is not None:
        workflow_registry.get(manifest.workflow.name, manifest.workflow.version)
    for component_name in manifest.components:
        component = load_skill_package(component_name, validate_bindings=False)
        if component.manifest.status == "deprecated":
            raise ConfigurationError(
                "Skill references a deprecated component.",
                {
                    "skill_name": manifest.name,
                    "component": component_name,
                },
            )


def discover_skill_packages(
    skills_dir: Path = DEFAULT_SKILLS_DIR,
) -> list[SkillPackageInfo]:
    packages: list[SkillPackageInfo] = []
    for manifest_path in sorted(skills_dir.glob("*/skill.yaml")):
        package = load_skill_package(manifest_path.parent.name, skills_dir)
        manifest = package.manifest
        packages.append(
            SkillPackageInfo(
                name=manifest.name,
                description=package.instructions.description,
                version=manifest.version,
                kind=manifest.kind,
                status=manifest.status,
                workflow_name=manifest.workflow.name if manifest.workflow else None,
                output_schema=manifest.model.output_schema,
                components=manifest.components,
                stages=sorted(manifest.stages),
                tools=manifest.tools.allow,
                validators=[
                    *manifest.validators.pre,
                    *manifest.validators.output,
                    *manifest.validators.post,
                ],
                limits=manifest.limits,
                package_sha256=package.package_sha256,
            )
        )
    return packages


def _package_sha256(skill_dir: Path, component_dirs: list[Path] | None = None) -> str:
    digest = hashlib.sha256()
    roots = [(skill_dir.name, skill_dir)]
    roots.extend((f"component/{path.name}", path) for path in component_dirs or [])
    files: list[tuple[str, Path, Path]] = []
    for prefix, root in roots:
        files.extend(
            (prefix, root, path)
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    for prefix, root, path in sorted(
        files,
        key=lambda item: f"{item[0]}/{item[2].relative_to(item[1]).as_posix()}",
    ):
        relative = f"{prefix}/{path.relative_to(root).as_posix()}".encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
