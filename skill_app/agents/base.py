from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from skill_app.db.models import AgentTask, StepRun, WorkflowRun


@dataclass(frozen=True)
class AgentContext:
    db: Session
    task: AgentTask
    workflow_run: WorkflowRun
    step_run: StepRun
