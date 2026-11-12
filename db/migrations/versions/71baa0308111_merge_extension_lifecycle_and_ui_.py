"""Merge extension lifecycle and UI metrics heads

Revision ID: 71baa0308111
Revises: 2a4c6e8f0b1d, a59e7e8cb54e
Create Date: 2026-11-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71baa0308111'
down_revision: Union[str, None] = ('2a4c6e8f0b1d', 'a59e7e8cb54e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
