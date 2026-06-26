from __future__ import annotations

import yaml
from pydantic import ValidationError

from skill_app.schemas.game_config import GameConfigValidationReport, NPCBehaviorConfig
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.inputs import ValidateNPCBehaviorConfigInput


def validate_npc_behavior_yaml(content: str) -> GameConfigValidationReport:
    checks = [
        "yaml_syntax",
        "json_schema_shape",
        "numeric_ranges",
        "enum_values",
        "reference_integrity",
        "threshold_order",
        "schema_version",
    ]
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return GameConfigValidationReport(
            valid=False,
            checks=checks,
            errors=[f"Invalid YAML: {exc}"],
        )
    if not isinstance(raw, dict):
        return GameConfigValidationReport(
            valid=False,
            checks=checks,
            errors=["Configuration root must be an object."],
        )
    try:
        config = NPCBehaviorConfig.model_validate(raw)
    except ValidationError as exc:
        return GameConfigValidationReport(
            valid=False,
            schema_version=raw.get("schema_version"),
            checks=checks,
            errors=[
                f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_url=False)
            ],
        )
    errors: list[str] = []
    known_npcs = set(config.npc_overrides)
    for group_name, npc_ids in config.npc_groups.items():
        missing = sorted(set(npc_ids) - known_npcs)
        if missing:
            errors.append(f"npc_groups.{group_name}: unknown NPC references {missing}.")
    return GameConfigValidationReport(
        valid=not errors,
        schema_version=config.schema_version,
        checks=checks,
        errors=errors,
        normalized_config=config.model_dump(mode="json"),
    )


def validate_npc_behavior_config(
    _: ToolContext,
    args: ValidateNPCBehaviorConfigInput,
) -> ToolResult:
    report = validate_npc_behavior_yaml(args.content)
    return ToolResult(
        ok=report.valid,
        output=report.model_dump(mode="json"),
        error_type=None if report.valid else "npc_behavior_config_validation_error",
    )
