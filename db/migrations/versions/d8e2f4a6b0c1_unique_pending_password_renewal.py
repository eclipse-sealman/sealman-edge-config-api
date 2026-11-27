"""Keep one pending password renewal per device without discarding task data."""

from alembic import op
import sqlalchemy as sa


revision = "d8e2f4a6b0c1"
down_revision = "c8d2e4f6a1b3"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("LOCK TABLE password_renewal_tasks IN ACCESS EXCLUSIVE MODE"))
    # Preserve the earliest promised deadline; retain duplicate rows as canceled.
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT task_id, row_number() OVER (
                PARTITION BY device_id
                ORDER BY scheduled_time, created_at, task_id
            ) AS position
            FROM password_renewal_tasks WHERE status = 'Pending'
        )
        UPDATE password_renewal_tasks
        SET status = 'Canceled', updated_at = CURRENT_TIMESTAMP
        WHERE task_id IN (SELECT task_id FROM ranked WHERE position > 1)
    """))
    op.create_index(
        "uq_password_renewal_tasks_pending_device",
        "password_renewal_tasks", ["device_id"], unique=True,
        postgresql_where=sa.text("status = 'Pending'"),
        sqlite_where=sa.text("status = 'Pending'"),
    )


def downgrade():
    op.drop_index(
        "uq_password_renewal_tasks_pending_device", table_name="password_renewal_tasks"
    )
