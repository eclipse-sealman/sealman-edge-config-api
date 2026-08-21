"""add_device_template_config

Revision ID: 046f476ded5c
Revises: c3fd04c560ae
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '046f476ded5c'
down_revision: Union[str, None] = 'c3fd04c560ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_config",
        sa.Column("name", sa.Text(), primary_key=True, nullable=False),
        sa.Column("device_template_config", JSONB(), nullable=False, server_default="{}"),
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
    op.execute(
        "INSERT INTO platform_config (name, device_template_config) VALUES ('default', '{}'::jsonb)"
    )


def downgrade() -> None:
    op.drop_table("platform_config")
