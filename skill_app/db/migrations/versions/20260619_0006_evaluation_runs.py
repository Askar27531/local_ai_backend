"""add evaluation runs and result grouping

Revision ID: 20260619_0006
Revises: 20260619_0005
Create Date: 2026-06-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260619_0006"
down_revision: str | None = "20260619_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("suite", sa.String(length=120), nullable=False),
        sa.Column("dataset_version", sa.String(length=80), nullable=False),
        sa.Column("dataset_sha256", sa.String(length=64), nullable=False),
        sa.Column("app_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed_cases", sa.Integer(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_evaluation_runs_status"), "evaluation_runs", ["status"], unique=False)
    op.create_index(op.f("ix_evaluation_runs_suite"), "evaluation_runs", ["suite"], unique=False)
    op.create_index(op.f("ix_evaluation_runs_task_id"), "evaluation_runs", ["task_id"], unique=False)
    with op.batch_alter_table("evaluation_results") as batch_op:
        batch_op.add_column(sa.Column("evaluation_run_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_evaluation_results_run_id",
            "evaluation_runs",
            ["evaluation_run_id"],
            ["id"],
        )
        batch_op.create_index("ix_evaluation_results_evaluation_run_id", ["evaluation_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("evaluation_results") as batch_op:
        batch_op.drop_index("ix_evaluation_results_evaluation_run_id")
        batch_op.drop_constraint("fk_evaluation_results_run_id", type_="foreignkey")
        batch_op.drop_column("evaluation_run_id")
    op.drop_index(op.f("ix_evaluation_runs_task_id"), table_name="evaluation_runs")
    op.drop_index(op.f("ix_evaluation_runs_suite"), table_name="evaluation_runs")
    op.drop_index(op.f("ix_evaluation_runs_status"), table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
