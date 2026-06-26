"""add skill component instruction snapshots

Revision ID: 20260623_0008
Revises: 20260623_0007
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260623_0008"
down_revision: str | None = "20260623_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skill_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "instruction_snapshots",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "component_instruction_sha256s",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_runs") as batch_op:
        batch_op.drop_column("component_instruction_sha256s")
        batch_op.drop_column("instruction_snapshots")
