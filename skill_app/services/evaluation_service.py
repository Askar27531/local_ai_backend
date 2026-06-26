from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skill_app.config import get_settings
from skill_app.db.models import (
    AgentTask,
    ApprovalRequest,
    EvaluationResult,
    EvaluationRun,
    ModelCall,
    WorkflowRun,
)
from skill_app.domain.errors import EntityNotFound
from skill_app.domain.status import ApprovalStatus, RunStatus, TaskStatus
from skill_app.evaluations.runners import config as config_runner
from skill_app.evaluations.runners import patch as patch_runner
from skill_app.evaluations.runners import planning as planning_runner
from skill_app.evaluations.runners import review as review_runner
from skill_app.evaluations.runners.common import DATASET_ROOT, dataset_digest, load_jsonl
from skill_app.services import event_service

SUITES: dict[str, tuple[str, Callable[[dict], dict]]] = {
    "planning": ("planning_cases.jsonl", planning_runner.run_case),
    "config": ("config_cases.jsonl", config_runner.run_case),
    "patch": ("patch_cases.jsonl", patch_runner.run_case),
    "review": ("code_review_cases.jsonl", review_runner.run_case),
}
EVALUATORS = {
    "planning": "planner",
    "config": "config_generator",
    "patch": "code_generator",
    "review": "review_agent",
}


def run_evaluation(
    db: Session,
    *,
    suite: str,
    dataset_version: str,
) -> EvaluationRun:
    selected = list(SUITES) if suite == "all" else [suite]
    paths = [DATASET_ROOT / SUITES[name][0] for name in selected]
    previous = (
        db.execute(
            select(EvaluationRun)
            .where(EvaluationRun.suite == suite, EvaluationRun.status == "succeeded")
            .order_by(EvaluationRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    task = _create_evaluation_task(db, suite)
    run = EvaluationRun(
        id=str(uuid.uuid4()),
        task_id=task.id,
        suite=suite,
        dataset_version=dataset_version,
        dataset_sha256=dataset_digest(paths),
        app_version=get_settings().app_version,
        status="running",
        total_cases=0,
        passed_cases=0,
        failed_cases=0,
        summary={},
    )
    db.add(run)
    db.commit()
    metric_values: dict[str, list[float]] = defaultdict(list)
    failed_cases: list[dict[str, Any]] = []
    total = passed = 0
    try:
        for suite_name in selected:
            filename, runner = SUITES[suite_name]
            for case in load_jsonl(filename):
                total += 1
                outcome = runner(case)
                if outcome["passed"]:
                    passed += 1
                else:
                    failed_cases.append(
                        {
                            "suite": suite_name,
                            "case_id": outcome["case_id"],
                            "details": outcome["details"],
                        }
                    )
                _record_case_results(db, run, task, suite_name, outcome)
                for metric_name, value in outcome["metrics"].items():
                    metric_values[f"{suite_name}.{metric_name}"].append(float(value))
        operational = _operational_metrics(db)
        for metric_name, value in operational.items():
            metric_values[metric_name].append(value)
            _add_result(
                db,
                run=run,
                task=task,
                evaluator=metric_name.split(".", 1)[0],
                metric_name=metric_name,
                score=value,
                verdict="pass",
                details={"source": "database_history"},
            )
        summary = {
            "case_pass_rate": passed / total if total else 0,
            "metrics": {
                name: round(statistics.fmean(values), 6)
                for name, values in sorted(metric_values.items())
            },
            "failed_cases": failed_cases,
        }
        summary["comparison"] = _compare_metrics(
            summary["metrics"],
            previous.summary.get("metrics", {}) if previous is not None else {},
            previous.id if previous is not None else None,
        )
        run.status = "succeeded"
        run.total_cases = total
        run.passed_cases = passed
        run.failed_cases = total - passed
        run.summary = summary
        run.finished_at = datetime.now()
        task.status = TaskStatus.SUCCEEDED
        event_service.record_event(
            db,
            task_id=task.id,
            event_type="evaluation_completed",
            actor_type="system",
            actor_name="evaluation_service",
            payload={
                "evaluation_run_id": run.id,
                "suite": suite,
                "total_cases": total,
                "passed_cases": passed,
            },
        )
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        run.status = "failed"
        run.total_cases = total
        run.passed_cases = passed
        run.failed_cases = total - passed
        run.summary = {"error_type": type(exc).__name__, "message": str(exc), "failed_cases": failed_cases}
        run.finished_at = datetime.now()
        task.status = TaskStatus.FAILED
        db.commit()
        raise


def list_runs(db: Session, limit: int = 100) -> list[EvaluationRun]:
    stmt = select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_run(db: Session, run_id: str) -> EvaluationRun:
    run = db.get(EvaluationRun, run_id)
    if run is None:
        raise EntityNotFound(f"Evaluation run not found: {run_id}", {"evaluation_run_id": run_id})
    return run


def list_results(db: Session, run_id: str) -> list[EvaluationResult]:
    get_run(db, run_id)
    stmt = (
        select(EvaluationResult)
        .where(EvaluationResult.evaluation_run_id == run_id)
        .order_by(EvaluationResult.created_at.asc(), EvaluationResult.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def aggregate(db: Session, group_by: str) -> dict[str, dict[str, float | int | None]]:
    if group_by == "agent":
        rows = db.execute(
            select(
                EvaluationResult.evaluator,
                func.count(EvaluationResult.id),
                func.avg(EvaluationResult.score),
            ).group_by(EvaluationResult.evaluator)
        ).all()
        return {
            name: {"count": count, "average_score": round(float(score), 6) if score is not None else None}
            for name, count, score in rows
        }
    if group_by == "workflow":
        rows = db.execute(select(WorkflowRun)).scalars().all()
        groups: dict[str, list[WorkflowRun]] = defaultdict(list)
        for row in rows:
            groups[row.workflow_name].append(row)
        return {
            name: _workflow_group_metrics(items)
            for name, items in sorted(groups.items())
        }
    if group_by == "model_profile":
        rows = db.execute(
            select(
                ModelCall.profile_name,
                func.count(ModelCall.id),
                func.avg(ModelCall.latency_ms),
                func.avg(ModelCall.input_tokens),
                func.avg(ModelCall.output_tokens),
            ).group_by(ModelCall.profile_name)
        ).all()
        return {
            name: {
                "count": count,
                "average_latency_ms": round(float(latency), 3) if latency is not None else None,
                "average_input_tokens": round(float(input_tokens), 3) if input_tokens is not None else None,
                "average_output_tokens": round(float(output_tokens), 3) if output_tokens is not None else None,
            }
            for name, count, latency, input_tokens, output_tokens in rows
        }
    raise ValueError(f"Unsupported evaluation grouping: {group_by}")


def dataset_catalog() -> list[dict[str, Any]]:
    return [
        {
            "suite": name,
            "filename": filename,
            "cases": len(load_jsonl(filename)),
            "sha256": dataset_digest([DATASET_ROOT / filename]),
        }
        for name, (filename, _) in sorted(SUITES.items())
    ]


def _create_evaluation_task(db: Session, suite: str) -> AgentTask:
    task = AgentTask(
        id=str(uuid.uuid4()),
        project_id="skill-app-evaluation",
        title=f"Offline evaluation: {suite}",
        request=f"Run deterministic offline evaluation suite: {suite}",
        task_type="evaluation",
        workflow_name=None,
        status=TaskStatus.RUNNING,
        risk_level="low",
        created_by="evaluation_service",
    )
    db.add(task)
    event_service.record_event(
        db,
        task_id=task.id,
        event_type="evaluation_started",
        actor_type="system",
        actor_name="evaluation_service",
        payload={"suite": suite},
    )
    db.commit()
    db.refresh(task)
    return task


def _record_case_results(
    db: Session,
    run: EvaluationRun,
    task: AgentTask,
    suite: str,
    outcome: dict[str, Any],
) -> None:
    for metric_name, score in outcome["metrics"].items():
        _add_result(
            db,
            run=run,
            task=task,
            evaluator=EVALUATORS[suite],
            metric_name=metric_name,
            score=float(score),
            verdict="pass" if outcome["passed"] else "fail",
            details={
                "case_id": outcome["case_id"],
                "case_passed": outcome["passed"],
                **outcome["details"],
            },
        )
    db.commit()


def _add_result(
    db: Session,
    *,
    run: EvaluationRun,
    task: AgentTask,
    evaluator: str,
    metric_name: str,
    score: float,
    verdict: str,
    details: dict[str, Any],
) -> None:
    db.add(
        EvaluationResult(
            id=str(uuid.uuid4()),
            evaluation_run_id=run.id,
            task_id=task.id,
            evaluator=evaluator,
            metric_name=metric_name,
            score=score,
            verdict=verdict,
            details=details,
        )
    )


def _operational_metrics(db: Session) -> dict[str, float]:
    workflow_runs = list(db.execute(select(WorkflowRun)).scalars().all())
    production_runs = [run for run in workflow_runs if run.workflow_name == "game_feature"]
    approvals = list(db.execute(select(ApprovalRequest)).scalars().all())
    model_calls = list(db.execute(select(ModelCall)).scalars().all())
    metrics: dict[str, float] = {}
    if production_runs:
        metrics["workflow.task_success_rate"] = sum(
            run.status == RunStatus.SUCCEEDED for run in production_runs
        ) / len(production_runs)
        metrics["workflow.first_pass_success_rate"] = sum(
            run.status == RunStatus.SUCCEEDED and run.state_snapshot.get("repair_count", 0) == 0
            for run in production_runs
        ) / len(production_runs)
        metrics["workflow.average_repair_count"] = statistics.fmean(
            float(run.state_snapshot.get("repair_count", 0)) for run in production_runs
        )
        durations = [
            (run.finished_at - run.started_at).total_seconds()
            for run in production_runs
            if run.started_at is not None and run.finished_at is not None
        ]
        metrics["workflow.average_duration_seconds"] = statistics.fmean(durations) if durations else 0
    resolved = [approval for approval in approvals if approval.status != ApprovalStatus.PENDING]
    metrics["workflow.human_approval_rate"] = (
        sum(approval.status == ApprovalStatus.APPROVED for approval in resolved) / len(resolved)
        if resolved
        else 0
    )
    if model_calls:
        metrics["model_profile.call_success_rate"] = sum(
            call.status == "succeeded" for call in model_calls
        ) / len(model_calls)
    return metrics


def _workflow_group_metrics(runs: list[WorkflowRun]) -> dict[str, float | int | None]:
    completed = [run for run in runs if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}]
    durations = [
        (run.finished_at - run.started_at).total_seconds()
        for run in completed
        if run.started_at is not None and run.finished_at is not None
    ]
    return {
        "count": len(runs),
        "success_rate": (
            sum(run.status == RunStatus.SUCCEEDED for run in completed) / len(completed)
            if completed
            else None
        ),
        "average_duration_seconds": round(statistics.fmean(durations), 3) if durations else None,
        "average_repair_count": round(
            statistics.fmean(float(run.state_snapshot.get("repair_count", 0)) for run in runs),
            3,
        ),
    }


def _compare_metrics(
    current: dict[str, float],
    previous: dict[str, float],
    previous_run_id: str | None,
) -> dict[str, Any]:
    lower_is_better = {
        "planning.irrelevant_step_rate",
        "config.manual_modified_fields",
        "review.false_positive_rate",
        "workflow.average_repair_count",
        "workflow.average_duration_seconds",
    }
    deltas: dict[str, float] = {}
    regressions: list[dict[str, Any]] = []
    for name, value in current.items():
        if name not in previous:
            continue
        delta = round(value - float(previous[name]), 6)
        deltas[name] = delta
        regressed = delta > 0 if name in lower_is_better else delta < 0
        if regressed:
            regressions.append(
                {
                    "metric_name": name,
                    "previous": previous[name],
                    "current": value,
                    "delta": delta,
                }
            )
    return {
        "previous_run_id": previous_run_id,
        "deltas": deltas,
        "regressions": regressions,
    }
