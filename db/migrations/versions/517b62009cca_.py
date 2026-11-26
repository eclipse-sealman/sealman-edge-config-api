"""Merge module-config template groups and password renewal branches.

Revision ID: 517b62009cca
Revises: b8d4f1a6c2e5, d8e2f4a6b0c1
Create Date: 2026-11-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '517b62009cca'
down_revision: Union[str, None] = ('b8d4f1a6c2e5', 'd8e2f4a6b0c1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
