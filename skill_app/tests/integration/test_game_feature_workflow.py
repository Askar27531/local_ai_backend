from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from skill_app.agents import game_feature as game_feature_agent
from skill_app.config import get_settings
from skill_app.main import app
from skill_app.schemas.code_generation import RepairEvidencePack
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.services import model_gateway
from skill_app.services.model_providers import FakeModelProvider


class RepairingGenerator:
    def generate(
        self,
        *,
        task_id: str,
        request: str,
        task_type: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context=None,
        relevant_paths: list[str] | None = None,
    ) -> CandidateSpec:
        content = (
            "def broken(:\n"
            if repair_count == 0
            else (
                "def build_repaired() -> dict[str, object]:\n"
                '    return {"feature_id": "repaired", "enabled": True}\n'
            )
        )
        return CandidateSpec(
            kind="python_patch",
            relative_path=f"generated/{task_id}/repair_demo.py",
            content=content,
            summary=f"attempt {repair_count}",
        )


class AlwaysInvalidGenerator:
    def generate(
        self,
        *,
        task_id: str,
        request: str,
        task_type: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context=None,
        relevant_paths: list[str] | None = None,
    ) -> CandidateSpec:
        return CandidateSpec(
            kind="python_patch",
            relative_path=f"generated/{task_id}/invalid.py",
            content=f"def invalid_{repair_count}(:\n",
            summary="always invalid",
        )


class BehavioralDefectGenerator:
    def generate(
        self,
        *,
        task_id: str,
        request: str,
        task_type: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context=None,
        relevant_paths: list[str] | None = None,
    ) -> CandidateSpec:
        enabled = repair_count > 0
        return CandidateSpec(
            kind="python_patch",
            relative_path=f"generated/{task_id}/behavior.py",
            content=(
                "def build_behavior() -> dict[str, object]:\n"
                f'    return {{"feature_id": "behavior", "enabled": {enabled!r}}}\n'
            ),
            summary=f"behavior attempt {repair_count}",
        )


class ReviewEvidenceRepairGenerator:
    evidence: RepairEvidencePack | None = None

    def generate(
        self,
        *,
        task_id: str,
        request: str,
        task_type: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context=None,
        relevant_paths: list[str] | None = None,
    ) -> CandidateSpec:
        if repair_count == 0:
            content = (
                "def build_review_repair() -> dict[str, object]:\n"
                "    # TODO remove placeholder\n"
                '    return {"feature_id": "review-repair", "enabled": True}\n'
            )
        else:
            self.evidence = repair_evidence
            content = (
                "def build_review_repair() -> dict[str, object]:\n"
                '    return {"feature_id": "review-repair", "enabled": True}\n'
            )
        return CandidateSpec(
            kind="python_patch",
            relative_path=f"generated/{task_id}/review_repair.py",
            content=content,
            summary=f"review repair attempt {repair_count}",
        )


def teardown_function() -> None:
    game_feature_agent.reset_candidate_generator()
    model_gateway.register_provider("fake", FakeModelProvider())


def create_task(client: TestClient, request: str, *, risk_level: str = "low") -> dict:
    response = client.post(
        "/tasks",
        json={
            "project_id": "game-feature-demo",
            "title": "Game feature",
            "request": request,
            "risk_level": risk_level,
        },
    )
    assert response.status_code == 201
    return response.json()


def start_game_feature(client: TestClient, task_id: str):
    return client.post(
        f"/tasks/{task_id}/start",
        json={"workflow_name": "game_feature"},
    )


def test_game_feature_end_to_end_delivers_manifest_without_source_write():
    with TestClient(app) as client:
        task = create_task(client, "实现一个 Python 游戏功能并执行自动化验证")
        started = start_game_feature(client, task["id"])
        assert started.status_code == 201, started.text
        paused = started.json()
        assert paused["status"] == "paused"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "awaiting_artifact_approval"
        candidate_id = paused["state_snapshot"]["current_candidate_artifact_id"]

        approved = client.post(
            f"/approvals/{paused['state_snapshot']['approval_id']}/resolve",
            json={"approved": True, "resolved_by": "producer"},
        )
        assert approved.status_code == 200, approved.text

        completed = client.get(f"/workflow-runs/{paused['id']}").json()
        assert completed["status"] == "succeeded"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "succeeded"
        manifest_id = completed["state_snapshot"]["delivery_manifest_artifact_id"]
        manifest = client.get(f"/artifacts/{manifest_id}/content").json()
        assert manifest["final_candidate_artifact_id"] == candidate_id
        assert manifest["source_project_modified"] is False
        assert manifest["diff_artifact_id"]
        assert manifest["test_report_artifact_ids"]
        assert manifest["test_plan_artifact_ids"]
        assert manifest["test_patch_artifact_ids"]
        assert manifest["code_patch_artifact_ids"]
        assert manifest["code_explanation_artifact_ids"]
        assert manifest["review_report_artifact_ids"]
        assert client.get(f"/artifacts/{candidate_id}").json()["approval_status"] == "approved"
        source_candidate = (
            get_settings().project_root
            / "generated"
            / task["id"]
            / "game_feature.py"
        )
        assert not source_candidate.exists()

        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        types = {artifact["artifact_type"] for artifact in artifacts}
        assert {
            "plan_json",
            "plan_markdown",
            "candidate_code",
            "code_patch",
            "code_explanation",
            "test_plan",
            "test_patch",
            "test_report",
            "review_report",
            "workspace_diff",
            "delivery_manifest",
        } <= types
        calls = client.get(f"/tasks/{task['id']}/model-calls").json()
        assert [call["prompt_version"] for call in calls] == [
            "planner-v1",
            "code-generator-v1",
            "test-generator-v1",
            "code-reviewer-v1",
        ]


def test_config_request_generates_structured_config_candidate():
    with TestClient(app) as client:
        task = create_task(client, "生成游戏数值配置 JSON")
        paused = start_game_feature(client, task["id"]).json()
        candidate = yaml.safe_load(client.get(
            f"/artifacts/{paused['state_snapshot']['current_candidate_artifact_id']}/content"
        ).text)
        assert candidate["schema_version"] == 1
        assert candidate["feature"] == "npc_behavior"
        assert candidate["thresholds"]["warning"] < candidate["thresholds"]["refuse"]
        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        types = {artifact["artifact_type"] for artifact in artifacts}
        assert {
            "candidate_config",
            "normalized_config",
            "config_validation_report",
            "config_explanation",
        } <= types


def test_validation_failure_repairs_once_and_retains_all_versions():
    game_feature_agent.set_candidate_generator(RepairingGenerator())
    with TestClient(app) as client:
        task = create_task(client, "实现 Python 代码功能")
        paused = start_game_feature(client, task["id"])
        assert paused.status_code == 201, paused.text
        state = paused.json()["state_snapshot"]
        assert state["repair_count"] == 1
        assert len(state["candidate_artifact_ids"]) == 2
        assert len(state["validation_artifact_ids"]) == 2
        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        candidates = [artifact for artifact in artifacts if artifact["artifact_type"] == "candidate_code"]
        assert [artifact["version"] for artifact in candidates] == [1, 2]
        assert candidates[1]["artifact_metadata"]["parent_candidate_artifact_id"] == candidates[0]["id"]
        assert candidates[1]["artifact_metadata"]["previous_sha256"]


def test_generated_test_catches_behavioral_defect_and_forces_repair():
    game_feature_agent.set_candidate_generator(BehavioralDefectGenerator())
    with TestClient(app) as client:
        task = create_task(client, "实现 Python 行为功能")
        paused = start_game_feature(client, task["id"])
        assert paused.status_code == 201, paused.text
        state = paused.json()["state_snapshot"]
        assert state["repair_count"] == 1
        reports = [
            artifact
            for artifact in client.get(f"/tasks/{task['id']}/artifacts").json()
            if artifact["artifact_type"] == "test_report"
        ]
        first_report = client.get(f"/artifacts/{reports[0]['id']}/content").json()
        assert not first_report["valid"]
        assert "Generated pytest failed." in first_report["errors"]


def test_review_repair_receives_complete_evidence_pack():
    generator = ReviewEvidenceRepairGenerator()
    game_feature_agent.set_candidate_generator(generator)
    with TestClient(app) as client:
        task = create_task(client, "实现 Python 功能并移除所有 TODO")
        paused = start_game_feature(client, task["id"])
        assert paused.status_code == 201, paused.text
        state = paused.json()["state_snapshot"]
        assert state["repair_count"] == 1
        evidence = generator.evidence
        assert evidence is not None
        assert "TODO" in evidence.previous_content
        assert evidence.previous_path.endswith("review_repair.py")
        assert evidence.previous_sha256
        assert evidence.test_plan["cases"]
        assert "def test_" in evidence.test_content
        assert evidence.test_report["passed"] is True
        assert evidence.review_findings[0]["category"] == "completeness"
        assert evidence.review_findings[0]["blocking"] is True
        assert evidence.acceptance_criteria


def test_repair_limit_fails_and_requests_manual_takeover():
    game_feature_agent.set_candidate_generator(AlwaysInvalidGenerator())
    with TestClient(app) as client:
        task = create_task(client, "实现 Python 代码功能")
        response = start_game_feature(client, task["id"])
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "workflow_execution_error"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "failed"
        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        candidates = [artifact for artifact in artifacts if artifact["artifact_type"] == "candidate_code"]
        assert len(candidates) == 3
        events = client.get(f"/tasks/{task['id']}/events").json()
        assert "manual_takeover_required" in [event["event_type"] for event in events]


def test_artifact_approval_rejection_cancels_delivery():
    with TestClient(app) as client:
        task = create_task(client, "生成游戏配置 JSON")
        run = start_game_feature(client, task["id"]).json()
        response = client.post(
            f"/approvals/{run['state_snapshot']['approval_id']}/resolve",
            json={"approved": False, "resolved_by": "producer", "comment": "Needs changes."},
        )
        assert response.status_code == 200
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "cancelled"
        assert client.get(f"/workflow-runs/{run['id']}").json()["status"] == "cancelled"
        candidate_id = run["state_snapshot"]["current_candidate_artifact_id"]
        assert client.get(f"/artifacts/{candidate_id}").json()["approval_status"] == "rejected"
        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        assert "delivery_manifest" not in [artifact["artifact_type"] for artifact in artifacts]


def test_high_risk_game_feature_has_plan_and_artifact_approval_gates():
    with TestClient(app) as client:
        task = create_task(client, "实现核心 Python 游戏功能", risk_level="high")
        plan_pause = start_game_feature(client, task["id"]).json()
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "awaiting_plan_approval"

        plan_approved = client.post(
            f"/approvals/{plan_pause['state_snapshot']['approval_id']}/resolve",
            json={"approved": True, "resolved_by": "lead"},
        )
        assert plan_approved.status_code == 200
        artifact_pause = client.get(f"/workflow-runs/{plan_pause['id']}").json()
        assert artifact_pause["status"] == "paused"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "awaiting_artifact_approval"
        assert artifact_pause["state_snapshot"]["current_candidate_artifact_id"]


def test_game_feature_reuses_a_completed_planner_workflow():
    with TestClient(app) as client:
        task = create_task(client, "实现 Python 游戏功能和测试")
        planner = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        )
        assert planner.status_code == 201
        assert planner.json()["status"] == "succeeded"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "running"

        execution = start_game_feature(client, task["id"])
        assert execution.status_code == 201, execution.text
        assert execution.json()["status"] == "paused"
        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        assert len([artifact for artifact in artifacts if artifact["artifact_type"] == "plan_json"]) == 1


def test_config_delivery_contains_normalized_json_explanation_and_config_diff():
    request = "生成 NPC 行为配置，窗口 240 秒，增量 12，警告 55，拒绝 90"
    with TestClient(app) as client:
        task = create_task(client, request)
        paused = start_game_feature(client, task["id"]).json()
        approved = client.post(
            f"/approvals/{paused['state_snapshot']['approval_id']}/resolve",
            json={"approved": True, "resolved_by": "designer"},
        )
        assert approved.status_code == 200
        completed = client.get(f"/workflow-runs/{paused['id']}").json()
        manifest = client.get(
            f"/artifacts/{completed['state_snapshot']['delivery_manifest_artifact_id']}/content"
        ).json()
        assert manifest["normalized_config_artifact_id"]
        assert manifest["config_explanation_artifact_id"]
        assert manifest["config_diff_artifact_id"]

        normalized = client.get(
            f"/artifacts/{manifest['normalized_config_artifact_id']}/content"
        ).json()
        assert normalized["rules"]["repeated_question"] == {
            "base_increment": 12,
            "window_seconds": 240,
        }
        assert normalized["thresholds"] == {"refuse": 90, "warning": 55}
        explanation = client.get(
            f"/artifacts/{manifest['config_explanation_artifact_id']}/content"
        ).text
        assert request in explanation
        config_diff = client.get(
            f"/artifacts/{manifest['config_diff_artifact_id']}/content"
        ).text
        assert "npc_behavior.yaml" in config_diff


def test_invalid_thresholds_are_repaired_deterministically():
    with TestClient(app) as client:
        task = create_task(client, "生成 NPC 配置，警告 95，拒绝 60")
        paused = start_game_feature(client, task["id"]).json()
        state = paused["state_snapshot"]
        assert state["repair_count"] == 1
        assert len(state["candidate_artifact_ids"]) == 2
        final_yaml = client.get(
            f"/artifacts/{state['current_candidate_artifact_id']}/content"
        ).text
        final_config = yaml.safe_load(final_yaml)
        assert final_config["thresholds"] == {"warning": 59, "refuse": 60}
