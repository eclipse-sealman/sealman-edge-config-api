"""add slot declarations to module config template versions

Revision ID: d5e7a9c1b3f2
Revises: e3a7c9d1f502
Create Date: 2026-11-24

Each version may declare, per endpoint slot, the endpoint and service types allowed for it.
Existing versions get an empty list, which means unrestricted.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d5e7a9c1b3f2"
down_revision: Union[str, None] = "e3a7c9d1f502"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("module_config_template_versions",
                  sa.Column("slots", JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("module_config_template_versions", "slots")
