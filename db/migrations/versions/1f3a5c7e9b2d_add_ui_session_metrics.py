"""add ui session metrics table

Revision ID: 1f3a5c7e9b2d
Revises: e1a2b3c4d5f6
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "1f3a5c7e9b2d"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ui_session_metrics",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("metric_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_ui_session_metrics_value", "ui_session_metrics", ["metric_value"])


def downgrade() -> None:
    op.drop_index("ix_ui_session_metrics_value", table_name="ui_session_metrics")
    op.drop_table("ui_session_metrics")
