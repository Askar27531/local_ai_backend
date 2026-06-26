"""add managed task workspaces

Revision ID: 20260619_0004
Revises: 20260618_0003
Create Date: 2026-06-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260619_0004"
down_revision: str | None = "20260618_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_workspaces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("source_root", sa.Text(), nullable=False),
        sa.Column("workspace_root", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("base_revision", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("copied_paths", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_root"),
    )
    op.create_index(op.f("ix_task_workspaces_task_id"), "task_workspaces", ["task_id"], unique=False)
    op.create_index(op.f("ix_task_workspaces_status"), "task_workspaces", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_workspaces_status"), table_name="task_workspaces")
    op.drop_index(op.f("ix_task_workspaces_task_id"), table_name="task_workspaces")
    op.drop_table("task_workspaces")
