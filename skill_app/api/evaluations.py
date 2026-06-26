from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from skill_app.db.database import get_db
from skill_app.schemas.evaluation import (
    EvaluationAggregate,
    EvaluationRunDetail,
    EvaluationRunRequest,
    EvaluationRunResponse,
)
from skill_app.services import evaluation_service

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post("/runs", response_model=EvaluationRunResponse, status_code=201)
def create_evaluation_run(
    payload: EvaluationRunRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return evaluation_service.run_evaluation(
        db,
        suite=payload.suite,
        dataset_version=payload.dataset_version,
    )


@router.get("/runs", response_model=list[EvaluationRunResponse])
def list_evaluation_runs(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    return evaluation_service.list_runs(db, limit)


@router.get("/runs/{run_id}", response_model=EvaluationRunDetail)
def get_evaluation_run(run_id: str, db: Annotated[Session, Depends(get_db)]):
    run = evaluation_service.get_run(db, run_id)
    return EvaluationRunDetail.model_validate(run).model_copy(
        update={"results": evaluation_service.list_results(db, run_id)}
    )


@router.get("/aggregate", response_model=EvaluationAggregate)
def aggregate_evaluations(
    group_by: Literal["agent", "workflow", "model_profile"],
    db: Annotated[Session, Depends(get_db)],
):
    return EvaluationAggregate(
        group_by=group_by,
        groups=evaluation_service.aggregate(db, group_by),
    )


@router.get("/datasets")
def list_evaluation_datasets():
    return evaluation_service.dataset_catalog()
