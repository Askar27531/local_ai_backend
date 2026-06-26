from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from skill_app.domain.errors import DuplicateEntity, ToolNotFound
from skill_app.schemas.tool import ToolContext, ToolDefinition, ToolResult

ToolHandler = Callable[[ToolContext, BaseModel], ToolResult]


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    input_model: type[BaseModel]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        definition: ToolDefinition,
        input_model: type[BaseModel],
        handler: ToolHandler,
    ) -> None:
        if definition.name in self._tools:
            raise DuplicateEntity(
                f"Tool already registered: {definition.name}",
                {"tool_name": definition.name},
            )
        enriched = definition.model_copy(update={"input_schema": input_model.model_json_schema()})
        self._tools[definition.name] = RegisteredTool(enriched, input_model, handler)

    def get(self, name: str) -> RegisteredTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"Tool not registered: {name}", {"tool_name": name})
        return tool

    def list_definitions(self) -> list[ToolDefinition]:
        return sorted((tool.definition for tool in self._tools.values()), key=lambda item: item.name)

    def clear(self) -> None:
        self._tools.clear()

    def execute_direct(self, name: str, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        validated = tool.input_model.model_validate(arguments)
        return tool.handler(context, validated)


tool_registry = ToolRegistry()
