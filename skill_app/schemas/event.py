from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    workflow_run_id: str | None
    step_run_id: str | None
    event_type: str
    actor_type: str
    actor_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
