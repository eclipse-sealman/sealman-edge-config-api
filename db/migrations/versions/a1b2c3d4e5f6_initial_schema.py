"""initial schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform",
        sa.Column("name", sa.Text(), primary_key=True, nullable=False),
        sa.Column("platform_meta", JSONB(), nullable=False),
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

    op.create_table(
        "devices",
        sa.Column("device_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("device_meta", JSONB(), nullable=False),
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

    # UNLOGGED: skips WAL writes for better write performance; data is lost on crash.
    # Appropriate for a cache table that is repopulated on startup.
    op.create_table(
        "device_snapshot_cache",
        sa.Column("cache_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("devices_json", JSONB(), nullable=False),
        sa.Column("device_count", sa.Integer(), nullable=False),
        sa.Column(
            "cached_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        prefixes=["UNLOGGED"],
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW view_device_snapshot AS
        SELECT
            elem ->> 'deviceName'   AS device_id,
            elem ->> 'deviceStatus' AS connection_state,
            c.cached_at
        FROM device_snapshot_cache c
        CROSS JOIN LATERAL jsonb_array_elements(c.devices_json) AS elem
        WHERE c.cache_key = 'current'
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    op.execute(
        """
        CREATE TRIGGER devices_updated_at
        BEFORE UPDATE ON devices
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column()
        """
    )

    op.execute(
        """
        CREATE TRIGGER platform_updated_at
        BEFORE UPDATE ON platform
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS platform_updated_at ON platform")
    op.execute("DROP TRIGGER IF EXISTS devices_updated_at ON devices")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    op.execute("DROP VIEW IF EXISTS view_device_snapshot")
    op.drop_table("device_snapshot_cache")
    op.drop_table("devices")
    op.drop_table("platform")
