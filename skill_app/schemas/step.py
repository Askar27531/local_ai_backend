from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from skill_app.domain.status import RunStatus


class StepRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_run_id: str
    step_key: str
    agent_name: str
    status: RunStatus
    input_data: dict[str, Any]
    output_data: dict[str, Any]
    retry_count: int
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
