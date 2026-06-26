from __future__ import annotations

from pathlib import Path

import pytest

from skill_app.services.skill_loader import (
    discover_skills,
    load_references,
    load_skill_package,
    parse_skill,
)

REQUIRED_SKILL_SECTIONS = {
    "art-asset-generator": ("## Objective", "## Completion criteria"),
    "code-generator": ("## Objective", "## Output contract", "## Repair procedure"),
    "code-reviewer": ("## Objective", "## Finding contract", "## Approval rules"),
    "game-config-generator": ("## Objective", "## Validation", "## Repair procedure"),
    "game-feature-planning": ("## Objective", "## Output contract", "## Reject invalid plans"),
    "game-text-writer": ("## Objective", "## Output contract", "## Repair procedure"),
    "lore-reviewer": ("## Objective", "## Finding contract", "## Approval rules"),
    "npc-app-feature": ("## Objective", "## Output contract", "## Repair procedure"),
    "test-generator": ("## Objective", "## Output contract", "## Prohibited behavior"),
}


def write_skill(directory: Path, folder: str, name: str, description: str = "Test skill") -> Path:
    skill_dir = directory / folder
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Instructions\n",
        encoding="utf-8",
    )
    return skill_md


def test_parse_skill_reads_frontmatter(tmp_path):
    skill_md = write_skill(tmp_path, "reviewer", "code-reviewer")

    skill = parse_skill(skill_md)

    assert skill.name == "code-reviewer"
    assert skill.description == "Test skill"
    assert "# Instructions" in skill.body


def test_parse_skill_rejects_missing_frontmatter(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("# Missing frontmatter", encoding="utf-8")

    with pytest.raises(ValueError, match="Missing YAML frontmatter"):
        parse_skill(skill_md)


def test_discover_skills_rejects_duplicate_names(tmp_path):
    write_skill(tmp_path, "first", "duplicate")
    write_skill(tmp_path, "second", "duplicate")

    with pytest.raises(ValueError, match="Duplicate skill name"):
        discover_skills(tmp_path)


def test_load_references_reads_named_markdown(tmp_path):
    skill_md = write_skill(tmp_path, "reviewer", "code-reviewer")
    references_dir = skill_md.parent / "references"
    references_dir.mkdir()
    (references_dir / "rules.md").write_text("# Rules", encoding="utf-8")
    skill = parse_skill(skill_md)

    references = load_references(skill, ["rules"])

    assert references == {"rules.md": "# Rules"}


def test_builtin_skills_have_complete_execution_contracts():
    discovered = {
        info.name: parse_skill(Path(info.path) / "SKILL.md")
        for info in discover_skills()
    }

    assert set(discovered) == set(REQUIRED_SKILL_SECTIONS)
    for name, required_sections in REQUIRED_SKILL_SECTIONS.items():
        skill = discovered[name]
        assert len(skill.body.splitlines()) >= 35
        assert len(skill.body.splitlines()) < 500
        for section in required_sections:
            assert section in skill.body


def test_npc_app_skill_package_binds_runtime_contract():
    package = load_skill_package("npc-app-feature")

    assert package.manifest.status == "active"
    assert package.manifest.workflow.name == "npc_app_feature"
    assert package.manifest.model.output_schema == "npc_app_patch_result"
    assert package.manifest.limits.max_files == 5
    assert package.manifest.workflow.max_repairs == 2
    assert len(package.package_sha256) == 64
    assert len(package.instruction_sha256) == 64


def test_every_builtin_skill_has_a_valid_machine_manifest():
    packages = {
        name: load_skill_package(name)
        for name in REQUIRED_SKILL_SECTIONS
    }

    assert set(packages) == set(REQUIRED_SKILL_SECTIONS)
    assert packages["npc-app-feature"].manifest.status == "active"
    assert packages["game-text-writer"].manifest.status == "active"
    assert packages["lore-reviewer"].manifest.status == "active"
    assert all(
        package.manifest.status == "draft"
        for name, package in packages.items()
        if name not in {"npc-app-feature", "game-text-writer", "lore-reviewer"}
    )


def test_game_text_package_snapshots_lore_component_and_stage_schemas():
    package = load_skill_package("game-text-writer")

    assert package.manifest.workflow.name == "game_text_production"
    assert package.manifest.components == ["lore-reviewer"]
    assert package.manifest.stages["writer"].output_schema == "game_text_candidate_set"
    assert package.manifest.limits.max_files == 5
    assert package.manifest.stages["reviewer"].output_schema == "game_text_review_result"
    assert "lore-reviewer" in package.component_instructions
    assert "lore-reviewer" in package.component_instruction_sha256s
    assert len(package.component_instruction_sha256s["lore-reviewer"]) == 64
