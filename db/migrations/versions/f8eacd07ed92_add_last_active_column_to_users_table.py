"""Add last_active column to users table

Revision ID: f8eacd07ed92
Revises: 0716880095d7
Create Date: 2026-06-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8eacd07ed92'
down_revision: Union[str, None] = '0716880095d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('last_active', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'last_active')
