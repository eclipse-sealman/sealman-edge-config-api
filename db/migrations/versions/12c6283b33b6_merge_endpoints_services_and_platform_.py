"""Merge endpoints/services and platform config/authorization branches

Revision ID: 12c6283b33b6
Revises: 74525cffd05c, 9b11c9dfe633
Create Date: 2026-07-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '12c6283b33b6'
down_revision: Union[str, None] = ('74525cffd05c', '9b11c9dfe633')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
