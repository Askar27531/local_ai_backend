"""add model call audit records

Revision ID: 20260619_0005
Revises: 20260619_0004
Create Date: 2026-06-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260619_0005"
down_revision: str | None = "20260619_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("step_run_id", sa.String(length=36), nullable=True),
        sa.Column("profile_name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("input_chars", sa.Integer(), nullable=False),
        sa.Column("output_chars", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("raw_output_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["raw_output_artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["step_run_id"], ["step_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_calls_profile_name"), "model_calls", ["profile_name"], unique=False)
    op.create_index(op.f("ix_model_calls_raw_output_artifact_id"), "model_calls", ["raw_output_artifact_id"])
    op.create_index(op.f("ix_model_calls_status"), "model_calls", ["status"], unique=False)
    op.create_index(op.f("ix_model_calls_step_run_id"), "model_calls", ["step_run_id"], unique=False)
    op.create_index(op.f("ix_model_calls_task_id"), "model_calls", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_calls_task_id"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_step_run_id"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_status"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_raw_output_artifact_id"), table_name="model_calls")
    op.drop_index(op.f("ix_model_calls_profile_name"), table_name="model_calls")
    op.drop_table("model_calls")
