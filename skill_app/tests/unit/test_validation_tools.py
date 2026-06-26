from __future__ import annotations

from skill_app.schemas.tool import ToolContext
from skill_app.tools.inputs import ValidateJsonInput, ValidateJsonSchemaInput, ValidateYamlInput
from skill_app.tools.validation import validate_json, validate_json_schema, validate_yaml

CONTEXT = ToolContext(
    task_id="task",
    workflow_run_id="run",
    step_run_id="step",
    agent_name="planner",
    project_root=".",
    workspace_root=".",
    timeout_seconds=60,
)


def test_json_and_yaml_validation():
    assert validate_json(CONTEXT, ValidateJsonInput(content='{"ok": true}')).ok
    assert not validate_json(CONTEXT, ValidateJsonInput(content="{bad")).ok
    assert validate_yaml(CONTEXT, ValidateYamlInput(content="ok: true")).ok
    assert not validate_yaml(CONTEXT, ValidateYamlInput(content="bad: [")).ok


def test_basic_json_schema_validation():
    schema = {
        "type": "object",
        "required": ["threshold"],
        "properties": {
            "threshold": {"type": "integer", "minimum": 1, "maximum": 100},
            "mode": {"type": "string", "enum": ["warn", "reject"]},
        },
    }

    valid = validate_json_schema(
        CONTEXT,
        ValidateJsonSchemaInput(instance={"threshold": 50, "mode": "warn"}, schema_definition=schema),
    )
    invalid = validate_json_schema(
        CONTEXT,
        ValidateJsonSchemaInput(instance={"threshold": 0, "mode": "other"}, schema_definition=schema),
    )

    assert valid.ok
    assert not invalid.ok
    assert len(invalid.output["errors"]) == 2
