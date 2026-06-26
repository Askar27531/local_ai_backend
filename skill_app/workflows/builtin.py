from __future__ import annotations

from skill_app.workflows.art_asset import build_art_asset_graph
from skill_app.workflows.foundation_smoke import build_foundation_smoke_graph
from skill_app.workflows.game_feature import build_game_feature_graph
from skill_app.workflows.game_text_production import build_game_text_production_graph
from skill_app.workflows.npc_app_feature import build_npc_app_feature_graph
from skill_app.workflows.planner import build_planner_graph
from skill_app.workflows.registry import WorkflowDefinition, workflow_registry


def register_builtin_workflows() -> None:
    existing = {(workflow.name, workflow.version) for workflow in workflow_registry.list()}
    definitions = [
        WorkflowDefinition(
            name="art_asset",
            version="1",
            description="Create and approve a visual brief, generate candidates, validate, select, and deliver art.",
            build_graph=build_art_asset_graph,
        ),
        WorkflowDefinition(
            name="foundation_smoke",
            version="1",
            description="Create a demo artifact, pause for approval, resume, and complete.",
            build_graph=build_foundation_smoke_graph,
        ),
        WorkflowDefinition(
            name="planner",
            version="1",
            description="Generate, validate, persist, and optionally approve a structured task plan.",
            build_graph=build_planner_graph,
            completes_task=False,
        ),
        WorkflowDefinition(
            name="game_feature",
            version="1",
            description="Plan, generate, validate, repair, review, approve, and deliver a game feature.",
            build_graph=build_game_feature_graph,
        ),
        WorkflowDefinition(
            name="game_text_production",
            version="1",
            description="Generate, validate, review, repair, approve, and deliver game_docs text.",
            build_graph=build_game_text_production_graph,
        ),
        WorkflowDefinition(
            name="npc_app_feature",
            version="1",
            description="Generate, test, review, repair, approve, and deliver a multi-file npc_app patch.",
            build_graph=build_npc_app_feature_graph,
        ),
    ]
    for definition in definitions:
        if (definition.name, definition.version) not in existing:
            workflow_registry.register(definition)
