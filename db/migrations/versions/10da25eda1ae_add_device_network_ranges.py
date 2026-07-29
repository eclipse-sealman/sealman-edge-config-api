"""add device network ranges

Revision ID: 10da25eda1ae
Revises: 12c6283b33b6
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '10da25eda1ae'
down_revision: Union[str, None] = '12c6283b33b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'device_network_ranges',
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('network_definition', sa.Text(), nullable=False),
        sa.Column('subnet_mask', sa.Integer(), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_id'),
    )


def downgrade() -> None:
    op.drop_table('device_network_ranges')
