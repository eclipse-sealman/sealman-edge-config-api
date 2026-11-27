"""add azure iot edge enabled to platform functions

Revision ID: d9e1f3a5b7c2
Revises: c8d2e4f6a1b3
Create Date: 2026-11-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e1f3a5b7c2'
down_revision: Union[str, None] = 'c8d2e4f6a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'platform_functions',
        sa.Column('azure_iot_edge_enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('platform_functions', 'azure_iot_edge_enabled')
