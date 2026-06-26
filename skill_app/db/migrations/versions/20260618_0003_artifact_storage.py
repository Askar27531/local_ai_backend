"""add artifact size and version uniqueness

Revision ID: 20260618_0003
Revises: 20260618_0002
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260618_0003"
down_revision: str | None = "20260618_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.add_column(sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch_op.create_unique_constraint(
            "uq_artifact_task_name_version",
            ["task_id", "logical_name", "version"],
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_constraint("uq_artifact_task_name_version", type_="unique")
        batch_op.drop_column("size_bytes")
