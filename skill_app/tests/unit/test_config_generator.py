from __future__ import annotations

import json
from pathlib import Path

import yaml

from skill_app.agents.config_generator import (
    NPCBehaviorConfigGenerator,
    normalized_json,
    render_config_explanation,
)
from skill_app.schemas.game_config import NPCBehaviorConfig
from skill_app.schemas.tool import ToolContext
from skill_app.tools.config_validation import validate_npc_behavior_config, validate_npc_behavior_yaml
from skill_app.tools.inputs import ValidateNPCBehaviorConfigInput


def test_generator_is_structurally_stable_for_same_request():
    generator = NPCBehaviorConfigGenerator()
    first = generator.generate(
        task_id="task-1",
        request="NPC 配置：窗口 180 秒，增量 8，警告 60，拒绝 100",
        repair_count=0,
        previous_content=None,
        validation_errors=[],
    )
    second = generator.generate(
        task_id="task-2",
        request="NPC 配置：窗口 180 秒，增量 8，警告 60，拒绝 100",
        repair_count=0,
        previous_content=None,
        validation_errors=[],
    )
    first_value = yaml.safe_load(first.content)
    second_value = yaml.safe_load(second.content)
    assert first_value == second_value
    assert list(first_value) == [
        "schema_version",
        "feature",
        "rules",
        "thresholds",
        "npc_overrides",
        "npc_groups",
    ]


def test_validator_rejects_threshold_order_ranges_enum_and_references():
    invalid = """
schema_version: 1
feature: npc_behavior
rules:
  repeated_question:
    window_seconds: 0
    base_increment: 101
thresholds:
  warning: 90
  refuse: 50
npc_overrides:
  guard:
    profile: impossible
npc_groups:
  town:
    - missing_npc
"""
    report = validate_npc_behavior_yaml(invalid)
    assert not report.valid
    assert any("window_seconds" in error for error in report.errors)
    assert any("base_increment" in error for error in report.errors)
    assert any("profile" in error for error in report.errors)
    assert any("warning threshold" in error for error in report.errors)

    reference_invalid = """
schema_version: 1
feature: npc_behavior
rules:
  repeated_question:
    window_seconds: 180
    base_increment: 8
thresholds:
  warning: 60
  refuse: 100
npc_overrides:
  guard:
    profile: strict
npc_groups:
  town:
    - guard
    - missing_npc
"""
    reference_report = validate_npc_behavior_yaml(reference_invalid)
    assert not reference_report.valid
    assert "missing_npc" in reference_report.errors[0]


def test_normalized_json_and_explanation_trace_requirement():
    content = NPCBehaviorConfig.model_validate(
        {
            "schema_version": 1,
            "feature": "npc_behavior",
            "rules": {
                "repeated_question": {
                    "window_seconds": 180,
                    "base_increment": 8,
                }
            },
            "thresholds": {"warning": 60, "refuse": 100},
            "npc_overrides": {},
            "npc_groups": {},
        }
    )
    normalized = json.loads(normalized_json(content))
    assert normalized["schema_version"] == 1
    explanation = render_config_explanation(content, "减少 NPC 重复问答")
    assert "减少 NPC 重复问答" in explanation
    assert "180 seconds" in explanation


def test_json_schema_asset_matches_versioned_config_contract():
    schema = json.loads(
        Path("skill_app/config_schemas/npc_behavior_v1.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["properties"]["feature"]["const"] == "npc_behavior"
    assert set(schema["required"]) == {
        "schema_version",
        "feature",
        "rules",
        "thresholds",
        "npc_overrides",
        "npc_groups",
    }


def test_config_validator_is_available_as_a_structured_tool():
    context = ToolContext(
        task_id="task",
        workflow_run_id="run",
        step_run_id="step",
        agent_name="test_agent",
        project_root=".",
        workspace_root=".",
        permissions=["validate_npc_behavior_config"],
        timeout_seconds=10,
    )
    result = validate_npc_behavior_config(
        context,
        ValidateNPCBehaviorConfigInput(
            content="""
schema_version: 1
feature: npc_behavior
rules:
  repeated_question:
    window_seconds: 180
    base_increment: 8
thresholds:
  warning: 60
  refuse: 100
npc_overrides: {}
npc_groups: {}
"""
        ),
    )
    assert result.ok
    assert result.output["valid"]
