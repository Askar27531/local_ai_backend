from __future__ import annotations

import json
import re
from typing import Any

import yaml

from skill_app.schemas.game_config import NPCBehaviorConfig
from skill_app.schemas.game_feature import CandidateSpec

DEFAULT_WINDOW_SECONDS = 180
DEFAULT_BASE_INCREMENT = 8
DEFAULT_WARNING = 60
DEFAULT_REFUSE = 100


class NPCBehaviorConfigGenerator:
    def generate(
        self,
        *,
        task_id: str,
        request: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
    ) -> CandidateSpec:
        values = _extract_values(request)
        if repair_count > 0 and previous_content:
            previous = yaml.safe_load(previous_content)
            if isinstance(previous, dict):
                values = _values_from_previous(previous, values)
            _repair_values(values)
        payload = {
            "schema_version": 1,
            "feature": "npc_behavior",
            "rules": {
                "repeated_question": {
                    "window_seconds": values["window_seconds"],
                    "base_increment": values["base_increment"],
                }
            },
            "thresholds": {
                "warning": values["warning"],
                "refuse": values["refuse"],
            },
            "npc_overrides": {},
            "npc_groups": {},
        }
        content = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        return CandidateSpec(
            kind="game_config",
            relative_path=f"generated/{task_id}/npc_behavior.yaml",
            content=content,
            summary=(
                "Generated NPC repeated-question behavior rules with deterministic thresholds"
                + (f"; repaired: {validation_errors}" if repair_count else ".")
            ),
        )


def render_config_explanation(config: NPCBehaviorConfig, request: str) -> str:
    rule = config.rules.repeated_question
    return "\n".join(
        [
            "# NPC Behavior Configuration",
            "",
            "## Requirement trace",
            "",
            request,
            "",
            "## Behavior",
            "",
            f"- Repeated-question window: {rule.window_seconds} seconds",
            f"- Base increment per repeated question: {rule.base_increment}",
            f"- Warning threshold: {config.thresholds.warning}",
            f"- Refusal threshold: {config.thresholds.refuse}",
            f"- NPC overrides: {len(config.npc_overrides)}",
            "",
            "The warning threshold is intentionally lower than the refusal threshold.",
            "All values were validated against NPC behavior schema version 1.",
            "",
        ]
    )


def normalized_json(config: NPCBehaviorConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _extract_values(request: str) -> dict[str, int]:
    return {
        "window_seconds": _extract_number(
            request,
            (r"(?:window|窗口)[^\d]{0,8}(\d+)", r"(\d+)\s*(?:秒|seconds?)"),
            DEFAULT_WINDOW_SECONDS,
        ),
        "base_increment": _extract_number(
            request,
            (r"(?:increment|增量|增加)[^\d]{0,8}(\d+)",),
            DEFAULT_BASE_INCREMENT,
        ),
        "warning": _extract_number(
            request,
            (r"(?:warning|警告|预警)[^\d]{0,8}(\d+)",),
            DEFAULT_WARNING,
        ),
        "refuse": _extract_number(
            request,
            (r"(?:refuse|拒绝)[^\d]{0,8}(\d+)",),
            DEFAULT_REFUSE,
        ),
    }


def _extract_number(request: str, patterns: tuple[str, ...], default: int) -> int:
    for pattern in patterns:
        match = re.search(pattern, request, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return default


def _values_from_previous(previous: dict[str, Any], fallback: dict[str, int]) -> dict[str, int]:
    repeated = previous.get("rules", {}).get("repeated_question", {})
    thresholds = previous.get("thresholds", {})
    return {
        "window_seconds": repeated.get("window_seconds", fallback["window_seconds"]),
        "base_increment": repeated.get("base_increment", fallback["base_increment"]),
        "warning": thresholds.get("warning", fallback["warning"]),
        "refuse": thresholds.get("refuse", fallback["refuse"]),
    }


def _repair_values(values: dict[str, int]) -> None:
    values["window_seconds"] = min(max(values["window_seconds"], 1), 3600)
    values["base_increment"] = min(max(values["base_increment"], 1), 100)
    values["warning"] = min(max(values["warning"], 0), 99)
    values["refuse"] = min(max(values["refuse"], 1), 100)
    if values["warning"] >= values["refuse"]:
        values["warning"] = max(0, values["refuse"] - 1)
