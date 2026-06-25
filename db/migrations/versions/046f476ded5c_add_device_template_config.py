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
    op.add_column(
        "platform",
        sa.Column(
            "device_template_config",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
    )
 
 
def downgrade() -> None:
    op.drop_column("platform", "device_template_config")
