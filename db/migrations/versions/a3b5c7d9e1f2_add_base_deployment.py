"""add base deployment

Revision ID: a3b5c7d9e1f2
Revises: f2a4c6e8b0d1
Create Date: 2026-11-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a3b5c7d9e1f2'
down_revision: Union[str, None] = 'f2a4c6e8b0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'base_deployment',
        sa.Column('id', sa.SmallInteger(), nullable=False),
        sa.Column('modules', postgresql.JSONB(), nullable=False),
        sa.Column('manifest', postgresql.JSONB(), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('id = 1', name='base_deployment_singleton'),
    )


def downgrade() -> None:
    op.drop_table('base_deployment')
