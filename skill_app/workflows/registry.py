from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from skill_app.domain.errors import DuplicateEntity, WorkflowNotFound
from skill_app.schemas.workflow import WorkflowInfo

GraphBuilder = Callable[[BaseCheckpointSaver], Any]


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: str
    description: str
    build_graph: GraphBuilder
    completes_task: bool = True

    def to_info(self) -> WorkflowInfo:
        return WorkflowInfo(name=self.name, version=self.version, description=self.description)


class WorkflowRegistry:
    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        key = (definition.name, definition.version)
        if key in self._definitions:
            raise DuplicateEntity(
                f"Workflow already registered: {definition.name}@{definition.version}",
                {"name": definition.name, "version": definition.version},
            )
        self._definitions[key] = definition

    def get(self, name: str, version: str = "1") -> WorkflowDefinition:
        definition = self._definitions.get((name, version))
        if definition is None:
            raise WorkflowNotFound(
                f"Workflow not found: {name}@{version}",
                {"name": name, "version": version},
            )
        return definition

    def list(self) -> list[WorkflowInfo]:
        definitions = sorted(self._definitions.values(), key=lambda item: (item.name, item.version))
        return [definition.to_info() for definition in definitions]


workflow_registry = WorkflowRegistry()
