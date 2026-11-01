"""add extensions.ui

Revision ID: f6a7b8c9d0e1
Revises: f1a2b3c4d5e6
Create Date: 2026-11-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("extensions", sa.Column("ui", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("extensions", "ui")
