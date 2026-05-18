"""add password renewal tasks table

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a2
Create Date: 2026-04-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_renewal_tasks",
        sa.Column("task_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("device_id", sa.Text(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("secret_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Enum(
            "Pending", "Canceled", "Completed", "Error",
            name="password_renewal_task_status",
        ), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_password_renewal_tasks_device_status",
        "password_renewal_tasks",
        ["device_id", "status"],
    )
    op.create_index(
        "ix_password_renewal_tasks_status_scheduled_time",
        "password_renewal_tasks",
        ["status", "scheduled_time"],
    )
    op.create_index(
        "ix_password_renewal_tasks_created_at",
        "password_renewal_tasks",
        ["created_at"],
    )

    op.execute(
        """
        CREATE TRIGGER password_renewal_tasks_updated_at
        BEFORE UPDATE ON password_renewal_tasks
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS password_renewal_tasks_updated_at ON password_renewal_tasks")
    op.drop_index("ix_password_renewal_tasks_created_at", table_name="password_renewal_tasks")
    op.drop_index("ix_password_renewal_tasks_status_scheduled_time", table_name="password_renewal_tasks")
    op.drop_index("ix_password_renewal_tasks_device_status", table_name="password_renewal_tasks")
    op.drop_table("password_renewal_tasks")
    op.execute("DROP TYPE IF EXISTS password_renewal_task_status")
