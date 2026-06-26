"""add immutable skill run snapshots

Revision ID: 20260623_0007
Revises: 20260619_0006
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260623_0007"
down_revision: str | None = "20260619_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=36), nullable=False),
        sa.Column("skill_name", sa.String(length=120), nullable=False),
        sa.Column("skill_version", sa.String(length=40), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("instruction_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id"),
    )
    op.create_index(op.f("ix_skill_runs_skill_name"), "skill_runs", ["skill_name"], unique=False)
    op.create_index(op.f("ix_skill_runs_status"), "skill_runs", ["status"], unique=False)
    op.create_index(op.f("ix_skill_runs_task_id"), "skill_runs", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_skill_runs_workflow_run_id"),
        "skill_runs",
        ["workflow_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_runs_workflow_run_id"), table_name="skill_runs")
    op.drop_index(op.f("ix_skill_runs_task_id"), table_name="skill_runs")
    op.drop_index(op.f("ix_skill_runs_status"), table_name="skill_runs")
    op.drop_index(op.f("ix_skill_runs_skill_name"), table_name="skill_runs")
    op.drop_table("skill_runs")
