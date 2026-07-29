"""add device extra scan ranges

Revision ID: 15b3bd023839
Revises: 10da25eda1ae
Create Date: 2026-07-27

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '15b3bd023839'
down_revision: Union[str, None] = '10da25eda1ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'device_extra_scan_ranges',
        sa.Column('range_id', sa.Text(), nullable=False, default=lambda: str(uuid.uuid4())),
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('network_definition', sa.Text(), nullable=False),
        sa.Column('subnet_mask', sa.Integer(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('range_id'),
    )
    op.create_index(
        'ix_device_extra_scan_ranges_device_id', 'device_extra_scan_ranges', ['device_id']
    )


def downgrade() -> None:
    op.drop_index('ix_device_extra_scan_ranges_device_id', table_name='device_extra_scan_ranges')
    op.drop_table('device_extra_scan_ranges')
