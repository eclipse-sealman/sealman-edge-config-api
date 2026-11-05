"""add ui session metrics table

Revision ID: 2a4c6e8f0b1d
Revises: f6a7b8c9d0e1
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2a4c6e8f0b1d"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The table may already exist in databases that ran an earlier feature branch.
    if sa.inspect(op.get_bind()).has_table("ui_session_metrics"):
        return
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
