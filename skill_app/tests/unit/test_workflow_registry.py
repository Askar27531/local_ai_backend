from __future__ import annotations

import pytest

from skill_app.domain.errors import DuplicateEntity, WorkflowNotFound
from skill_app.workflows.registry import WorkflowDefinition, WorkflowRegistry


def graph_builder(checkpointer):
    return checkpointer


def test_workflow_registry_registers_lists_and_gets_definition():
    registry = WorkflowRegistry()
    definition = WorkflowDefinition(
        name="demo",
        version="1",
        description="Demo workflow.",
        build_graph=graph_builder,
    )

    registry.register(definition)

    assert registry.get("demo", "1") is definition
    assert registry.list()[0].model_dump() == {
        "name": "demo",
        "version": "1",
        "description": "Demo workflow.",
    }


def test_workflow_registry_rejects_duplicate_definition():
    registry = WorkflowRegistry()
    definition = WorkflowDefinition("demo", "1", "Demo.", graph_builder)
    registry.register(definition)

    with pytest.raises(DuplicateEntity):
        registry.register(definition)


def test_workflow_registry_rejects_unknown_workflow():
    registry = WorkflowRegistry()

    with pytest.raises(WorkflowNotFound):
        registry.get("missing", "1")
