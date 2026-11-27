"""Persist password renewal retry eligibility across workers."""

from alembic import op
import sqlalchemy as sa


revision = "e3a7c9d1f502"
down_revision = "517b62009cca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "password_renewal_tasks",
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("password_renewal_tasks", "next_attempt_at")
