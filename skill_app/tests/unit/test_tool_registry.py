from __future__ import annotations

import pytest
from pydantic import BaseModel

from skill_app.domain.errors import DuplicateEntity, ToolNotFound
from skill_app.schemas.tool import ToolContext, ToolDefinition, ToolResult
from skill_app.tools.registry import ToolRegistry


class EchoInput(BaseModel):
    value: int


def echo_handler(_: ToolContext, arguments: BaseModel) -> ToolResult:
    return ToolResult(ok=True, output=arguments.model_dump())


def context() -> ToolContext:
    return ToolContext(
        task_id="task",
        workflow_run_id="run",
        step_run_id="step",
        agent_name="test_agent",
        project_root=".",
        workspace_root=".",
        timeout_seconds=60,
    )


def test_registry_registers_lists_validates_and_executes_tool():
    registry = ToolRegistry()
    definition = ToolDefinition(name="echo", description="Return arguments.")

    registry.register(definition, EchoInput, echo_handler)

    registered = registry.get("echo")
    assert registered.definition.input_schema["properties"]["value"]["type"] == "integer"
    assert registry.execute_direct("echo", context(), {"value": 3}).output == {"value": 3}


def test_registry_rejects_duplicate_tool_name():
    registry = ToolRegistry()
    definition = ToolDefinition(name="echo", description="Return arguments.")
    registry.register(definition, EchoInput, echo_handler)

    with pytest.raises(DuplicateEntity):
        registry.register(definition, EchoInput, echo_handler)


def test_registry_rejects_unknown_tool():
    registry = ToolRegistry()

    with pytest.raises(ToolNotFound):
        registry.get("missing")
