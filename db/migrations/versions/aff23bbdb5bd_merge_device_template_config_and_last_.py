"""Merge device template config and last_active migrations

Revision ID: aff23bbdb5bd
Revises: 046f476ded5c, f8eacd07ed92
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aff23bbdb5bd'
down_revision: Union[str, None] = ('046f476ded5c', 'f8eacd07ed92')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
