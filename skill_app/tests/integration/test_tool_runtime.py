from __future__ import annotations

import hashlib
import time

from fastapi.testclient import TestClient
from pydantic import BaseModel

from skill_app.db.database import SessionLocal
from skill_app.main import app
from skill_app.schemas.task import TaskCreate
from skill_app.schemas.tool import ToolContext, ToolDefinition, ToolResult
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import (
    policy_service,
    skill_service,
    step_service,
    task_service,
    workflow_service,
)
from skill_app.services.skill_loader import load_skill_package
from skill_app.tools.builtin import register_builtin_tools
from skill_app.tools.registry import tool_registry


def create_running_step(agent_name: str) -> tuple[str, str, str]:
    with SessionLocal() as db:
        task = task_service.create_task(
            db,
            TaskCreate(project_id="tool-demo", title="Tool runtime", request="Exercise tools."),
        )
        run = task_service.create_workflow_run(
            db,
            task,
            WorkflowRunCreate(workflow_name="foundation_smoke"),
        )
        workflow_service.start_run(db, task, run)
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key=f"{agent_name}_step",
            agent_name=agent_name,
        )
        return task.id, run.id, step.id


def execute(client: TestClient, ids: tuple[str, str, str], agent: str, tool: str, arguments: dict):
    task_id, run_id, step_id = ids
    return client.post(
        f"/tasks/{task_id}/tools/{tool}/execute",
        json={
            "workflow_run_id": run_id,
            "step_run_id": step_id,
            "agent_name": agent,
            "arguments": arguments,
        },
    )


def test_read_tools_and_every_call_is_audited():
    ids = create_running_step("planner")
    with TestClient(app) as client:
        response = execute(
            client,
            ids,
            "planner",
            "read_project_file",
            {"path": "skill_app/README.md", "max_chars": 200},
        )

        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["ok"]
        assert result["output"]["path"] == "skill_app/README.md"
        events = client.get(f"/tasks/{ids[0]}/events").json()
        event_types = [event["event_type"] for event in events]
        assert "tool_called" in event_types
        assert "tool_completed" in event_types


def test_list_search_and_read_range_tools():
    ids = create_running_step("planner")
    with TestClient(app) as client:
        listed = execute(
            client,
            ids,
            "planner",
            "list_project_files",
            {"pattern": "skill_app/*.py", "limit": 20},
        )
        searched = execute(
            client,
            ids,
            "planner",
            "search_project_text",
            {"query": "Game Production Agent Platform", "pattern": "skill_app/*.py"},
        )
        ranged = execute(
            client,
            ids,
            "planner",
            "read_file_range",
            {"path": "skill_app/main.py", "start_line": 1, "end_line": 5},
        )

        assert listed.status_code == 200
        assert "skill_app/main.py" in listed.json()["result"]["output"]["files"]
        assert searched.json()["result"]["output"]["count"] >= 1
        assert "from __future__" in ranged.json()["result"]["output"]["content"]


def test_planner_cannot_write_workspace():
    ids = create_running_step("planner")
    with TestClient(app) as client:
        response = execute(
            client,
            ids,
            "planner",
            "write_workspace_file",
            {"path": "candidate.txt", "content": "no"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "tool_permission_denied"
        events = client.get(f"/tasks/{ids[0]}/events").json()
        assert [event["event_type"] for event in events][-2:] == ["tool_requested", "tool_denied"]


def test_skill_policy_can_narrow_agent_tool_paths():
    ids = create_running_step("code_generator")
    with SessionLocal() as db:
        skill_service.create_skill_run(
            db,
            task_id=ids[0],
            workflow_run_id=ids[1],
            package=load_skill_package("npc-app-feature"),
        )

    with TestClient(app) as client:
        allowed = execute(
            client,
            ids,
            "code_generator",
            "read_project_file",
            {"path": "npc_app/__init__.py", "max_chars": 100},
        )
        denied = execute(
            client,
            ids,
            "code_generator",
            "read_project_file",
            {"path": "skill_app/README.md", "max_chars": 100},
        )

        assert allowed.status_code == 200, allowed.text
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "tool_permission_denied"


def test_game_text_skill_restricts_tools_to_game_docs():
    ids = create_running_step("game_text_writer")
    with SessionLocal() as db:
        skill_service.create_skill_run(
            db,
            task_id=ids[0],
            workflow_run_id=ids[1],
            package=load_skill_package("game-text-writer"),
        )

    with TestClient(app) as client:
        allowed = execute(
            client,
            ids,
            "game_text_writer",
            "read_project_file",
            {"path": "game_docs/vectorized/01_world_lore_世界观.md", "max_chars": 100},
        )
        denied = execute(
            client,
            ids,
            "game_text_writer",
            "read_project_file",
            {"path": "npc_app/__init__.py", "max_chars": 100},
        )

        assert allowed.status_code == 200, allowed.text
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "tool_permission_denied"


def test_unknown_tool_attempt_is_audited():
    ids = create_running_step("planner")
    with TestClient(app) as client:
        response = execute(client, ids, "planner", "missing_tool", {})

        assert response.status_code == 404
        events = client.get(f"/tasks/{ids[0]}/events").json()
        assert [event["event_type"] for event in events][-2:] == ["tool_requested", "tool_denied"]


def test_workspace_write_registers_artifact_and_diff():
    ids = create_running_step("code_generator")
    with TestClient(app) as client:
        written = execute(
            client,
            ids,
            "code_generator",
            "write_workspace_file",
            {"path": "skill_app/README.md", "content": "candidate readme"},
        )

        assert written.status_code == 200, written.text
        result = written.json()["result"]
        assert result["ok"]
        assert len(result["artifacts"]) == 1
        artifact = client.get(f"/artifacts/{result['artifacts'][0]}").json()
        assert artifact["artifact_metadata"]["tool_name"] == "write_workspace_file"

        diff = execute(
            client,
            ids,
            "code_generator",
            "create_unified_diff",
            {"path": "skill_app/README.md"},
        )
        assert diff.status_code == 200
        assert diff.json()["result"]["output"]["changed"]

        current = "candidate readme"
        patched = execute(
            client,
            ids,
            "code_generator",
            "apply_patch_to_workspace",
            {
                "path": "skill_app/README.md",
                "old_text": "candidate",
                "new_text": "patched",
                "expected_sha256": hashlib.sha256(current.encode()).hexdigest(),
            },
        )
        assert patched.status_code == 200
        assert patched.json()["result"]["ok"]


def test_path_escape_and_raw_command_input_are_blocked():
    ids = create_running_step("test_agent")
    with TestClient(app) as client:
        escaped = execute(
            client,
            ids,
            "test_agent",
            "write_workspace_file",
            {"path": "../escape.txt", "content": "blocked"},
        )
        raw_command = execute(
            client,
            ids,
            "test_agent",
            "run_pytest",
            {"command": "rm -rf /"},
        )

        assert escaped.status_code == 400
        assert escaped.json()["error"]["code"] == "unsafe_path"
        assert raw_command.status_code == 400
        assert raw_command.json()["error"]["code"] == "tool_input_validation_error"


class NoInput(BaseModel):
    pass


def slow_handler(_: ToolContext, __: BaseModel) -> ToolResult:
    time.sleep(1.2)
    return ToolResult(ok=True)


def huge_handler(_: ToolContext, __: BaseModel) -> ToolResult:
    return ToolResult(ok=True, stdout="x" * 20000, output={"content": "y" * 20000})


def test_timeout_and_output_truncation_are_uniform():
    register_builtin_tools()
    policy = policy_service.load_policy()
    for name, handler in [("test_slow_tool", slow_handler), ("test_huge_tool", huge_handler)]:
        if name not in [definition.name for definition in tool_registry.list_definitions()]:
            tool_registry.register(
                ToolDefinition(name=name, description=name, timeout_seconds=1),
                NoInput,
                handler,
            )
        if name not in policy.agents["test_agent"].allow:
            policy.agents["test_agent"].allow.append(name)

    slow_ids = create_running_step("test_agent")
    huge_ids = create_running_step("test_agent")
    with TestClient(app) as client:
        timed_out = execute(client, slow_ids, "test_agent", "test_slow_tool", {})
        huge = execute(client, huge_ids, "test_agent", "test_huge_tool", {})

        assert timed_out.status_code == 200
        assert timed_out.json()["result"]["error_type"] == "tool_timeout"
        assert not timed_out.json()["result"]["ok"]
        huge_result = huge.json()["result"]
        assert huge_result["output"]["truncated"]
        assert "[truncated" in huge_result["stdout"]


def test_structured_compile_and_pytest_tools():
    ids = create_running_step("test_agent")
    with TestClient(app) as client:
        module = execute(
            client,
            ids,
            "test_agent",
            "write_workspace_file",
            {"path": "demo.py", "content": "def add(a, b):\n    return a + b\n"},
        )
        test_file = execute(
            client,
            ids,
            "test_agent",
            "write_workspace_file",
            {
                "path": "test_demo.py",
                "content": "from demo import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
            },
        )
        assert module.status_code == 200
        assert test_file.status_code == 200

        compiled = execute(
            client,
            ids,
            "test_agent",
            "run_python_compile",
            {"paths": ["demo.py", "test_demo.py"]},
        )
        tested = execute(
            client,
            ids,
            "test_agent",
            "run_pytest",
            {"paths": ["test_demo.py"], "max_failures": 1},
        )

        assert compiled.status_code == 200
        assert compiled.json()["result"]["ok"]
        assert tested.status_code == 200
        assert tested.json()["result"]["ok"]


def approval_handler(_: ToolContext, __: BaseModel) -> ToolResult:
    return ToolResult(ok=True, output={"approved_execution": True})


def test_tool_can_require_explicit_approval():
    register_builtin_tools()
    name = "test_approval_tool"
    if name not in [definition.name for definition in tool_registry.list_definitions()]:
        tool_registry.register(
            ToolDefinition(name=name, description=name, approval_required=True),
            NoInput,
            approval_handler,
        )
    policy = policy_service.load_policy()
    if name not in policy.agents["code_generator"].allow:
        policy.agents["code_generator"].allow.append(name)

    ids = create_running_step("code_generator")
    with TestClient(app) as client:
        denied = execute(client, ids, "code_generator", name, {})
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "approval_required"

        artifact_response = execute(
            client,
            ids,
            "code_generator",
            "write_workspace_file",
            {"path": "approval.txt", "content": "candidate"},
        )
        artifact_id = artifact_response.json()["result"]["artifacts"][0]
        approval = client.post(
            f"/tasks/{ids[0]}/approvals",
            json={"artifact_id": artifact_id, "approval_type": f"tool:{name}"},
        ).json()
        resolved = client.post(
            f"/approvals/{approval['id']}/resolve",
            json={"approved": True, "resolved_by": "producer"},
        )
        assert resolved.status_code == 200

        allowed = execute(client, ids, "code_generator", name, {})
        assert allowed.status_code == 200
        assert allowed.json()["result"]["output"]["approved_execution"]
