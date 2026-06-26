from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_REQUEST = (
    "在 npc_app 中新增一个隔离的小型状态辅助函数，并生成对应 pytest。"
    "不要修改数据库结构，不要访问外部网络，保持改动最小。"
)
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class ApiError(RuntimeError):
    pass


class ApiTimeout(ApiError):
    pass


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> Any:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"{method} {path} returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"Cannot connect to {base_url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ApiTimeout(f"{method} {path} timed out after {timeout:.0f}s") from exc


def create_task(base_url: str, args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "project_id": args.project_id,
        "title": args.title,
        "request": args.request,
        "task_type": "npc_app_feature",
        "risk_level": args.risk_level,
        "created_by": "llm-code-modification-smoke",
    }
    return request_json(base_url, "POST", "/tasks", payload)


def start_skill(base_url: str, task_id: str, skill_version: str, timeout: float) -> dict[str, Any]:
    return request_json(
        base_url,
        "POST",
        f"/tasks/{task_id}/skills/npc-app-feature/start",
        {"version": skill_version},
        timeout=timeout,
    )


def list_workflow_runs(base_url: str, task_id: str) -> list[dict[str, Any]]:
    return request_json(base_url, "GET", f"/tasks/{task_id}/workflow-runs")


def recover_started_workflow(base_url: str, task_id: str, interval: float, max_wait: float) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        runs = list_workflow_runs(base_url, task_id)
        if runs:
            return runs[0]
        time.sleep(interval)
    raise ApiError(f"Start request timed out and no workflow run appeared for task {task_id}")


def poll_workflow(base_url: str, workflow_run_id: str, interval: float, max_wait: float) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait
    last_status = None
    last_step = None
    while True:
        workflow = request_json(base_url, "GET", f"/workflow-runs/{workflow_run_id}")
        status = workflow["status"]
        if status != last_status:
            print(f"workflow status: {status}")
            last_status = status
        steps = request_json(base_url, "GET", f"/workflow-runs/{workflow_run_id}/steps")
        running_steps = [step for step in steps if step["status"] == "running"]
        current_step = running_steps[-1]["step_key"] if running_steps else None
        if current_step and current_step != last_step:
            print(f"current step: {current_step}")
            last_step = current_step
        if status in TERMINAL_STATUSES or status == "paused":
            return workflow
        if time.monotonic() >= deadline:
            raise ApiError(f"Timed out waiting for workflow {workflow_run_id}; last status={status}")
        time.sleep(interval)


def resolve_approval(base_url: str, approval_id: str, approved: bool) -> dict[str, Any]:
    return request_json(
        base_url,
        "POST",
        f"/approvals/{approval_id}/resolve",
        {
            "approved": approved,
            "resolved_by": "llm-code-modification-smoke",
            "comment": "Automated smoke-test approval." if approved else "Automated smoke-test rejection.",
        },
    )


def print_summary(base_url: str, task_id: str, workflow: dict[str, Any]) -> None:
    artifacts = request_json(base_url, "GET", f"/tasks/{task_id}/artifacts")
    steps = request_json(base_url, "GET", f"/workflow-runs/{workflow['id']}/steps")
    model_calls = request_json(base_url, "GET", f"/tasks/{task_id}/model-calls")
    skill_runs = request_json(base_url, "GET", f"/tasks/{task_id}/skill-runs")
    events = request_json(base_url, "GET", f"/tasks/{task_id}/events")

    print("\nsummary")
    print(f"task_id: {task_id}")
    print(f"workflow_run_id: {workflow['id']}")
    print(f"workflow_status: {workflow['status']}")
    print(f"task_status: {request_json(base_url, 'GET', f'/tasks/{task_id}')['status']}")
    print(f"skill_runs: {len(skill_runs)}")
    print(f"model_calls: {len(model_calls)}")
    if model_calls:
        profiles = sorted({call.get("profile_name") for call in model_calls})
        print(f"model_profiles: {', '.join(profile for profile in profiles if profile)}")

    print("\nsteps")
    for step in steps:
        line = f"- {step['agent_name']} / {step['step_key']}: {step['status']}"
        if step.get("error_type"):
            line += f" ({step['error_type']}: {step.get('error_message')})"
        print(line)

    failures = [
        event for event in events
        if event["event_type"] in {"workflow_failed", "step_failed"}
    ]
    if failures:
        print("\nfailures")
        for event in failures[-5:]:
            payload = event.get("payload", {})
            print(
                "- "
                f"{event['event_type']}: "
                f"{payload.get('error_type', 'unknown')} - "
                f"{payload.get('error_message', '')}"
            )

    print("\nartifacts")
    for artifact in artifacts:
        print(
            "- "
            f"{artifact['artifact_type']} "
            f"name={artifact['logical_name']} "
            f"version={artifact['version']} "
            f"validation={artifact['validation_status']} "
            f"approval={artifact['approval_status']}"
        )

    state = workflow.get("state_snapshot", {})
    manifest_id = state.get("delivery_manifest_artifact_id")
    diff_id = state.get("diff_artifact_id")
    if manifest_id:
        print(f"\ndelivery_manifest_artifact_id: {manifest_id}")
    if diff_id:
        print(f"diff_artifact_id: {diff_id}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test the local LLM code modification path by starting the "
            "npc-app-feature skill package against a running skill_app API."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--project-id", default="local-ai-backend")
    parser.add_argument("--task-id", help="Attach to an existing task instead of creating a new one.")
    parser.add_argument("--workflow-run-id", help="Attach to an existing workflow run instead of starting a new one.")
    parser.add_argument("--title", default="LLM code modification smoke test")
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--risk-level", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--skill-version", default="1.0.0")
    parser.add_argument(
        "--start-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for the start API. Correct async servers should return quickly.",
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-wait", type=float, default=3600.0)
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve the delivery approval gate and wait for final completion.",
    )
    parser.add_argument(
        "--reject",
        action="store_true",
        help="Reject the delivery approval gate instead of approving it. Implies resolving the approval.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        health = request_json(args.base_url, "GET", "/health")
        print(f"connected: {health['app']} {health['version']} ({health['status']})")

        if args.workflow_run_id:
            workflow = request_json(args.base_url, "GET", f"/workflow-runs/{args.workflow_run_id}")
            task = request_json(args.base_url, "GET", f"/tasks/{workflow['task_id']}")
            print(f"attached workflow: {workflow['id']} ({workflow['workflow_name']})")
        else:
            task = (
                request_json(args.base_url, "GET", f"/tasks/{args.task_id}")
                if args.task_id
                else create_task(args.base_url, args)
            )
            print(f"{'attached' if args.task_id else 'created'} task: {task['id']}")
            if args.task_id:
                workflow = recover_started_workflow(
                    args.base_url,
                    task["id"],
                    args.poll_interval,
                    min(args.max_wait, 120.0),
                )
                print(f"recovered workflow: {workflow['id']} ({workflow['workflow_name']})")
            else:
                try:
                    workflow = start_skill(args.base_url, task["id"], args.skill_version, args.start_timeout)
                    print(f"started workflow: {workflow['id']} ({workflow['workflow_name']})")
                except ApiTimeout as exc:
                    print(f"warning: {exc}", file=sys.stderr)
                    print("trying to recover an already-created workflow run for this task...", file=sys.stderr)
                    workflow = recover_started_workflow(
                        args.base_url,
                        task["id"],
                        args.poll_interval,
                        min(args.max_wait, 120.0),
                    )
                    print(f"recovered workflow: {workflow['id']} ({workflow['workflow_name']})")

        workflow = poll_workflow(args.base_url, workflow["id"], args.poll_interval, args.max_wait)
        state = workflow.get("state_snapshot", {})
        approval_id = state.get("approval_id")

        if workflow["status"] == "paused" and approval_id:
            print(f"paused for approval: {approval_id}")
            if args.auto_approve or args.reject:
                approved = not args.reject
                decision = resolve_approval(args.base_url, approval_id, approved)
                print(f"approval resolved: {decision['status']}")
                workflow = poll_workflow(args.base_url, workflow["id"], args.poll_interval, args.max_wait)
            else:
                print("run again with --auto-approve to complete delivery, or resolve this approval manually.")

        print_summary(args.base_url, task["id"], workflow)
        return 0 if workflow["status"] in {"paused", "succeeded"} else 2
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
