"""Merge module config template groups and azure iot edge heads

Revision ID: e7f8a9b0c1d2
Revises: b8d4f1a6c2e5, d9e1f3a5b7c2
Create Date: 2026-11-24

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = ('b8d4f1a6c2e5', 'd9e1f3a5b7c2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
