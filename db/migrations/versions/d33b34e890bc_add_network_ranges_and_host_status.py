"""add network ranges and host status

Revision ID: d33b34e890bc
Revises: c3fd04c560ae
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd33b34e890bc'
down_revision: Union[str, None] = 'c3fd04c560ae'
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
    op.create_table(
        'device_host_status',
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('ip', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('changed_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_id', 'ip'),
    )
    op.create_table(
        'device_port_status',
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('ip', sa.Text(), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('changed_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_id', 'ip', 'port'),
    )


def downgrade() -> None:
    op.drop_table('device_port_status')
    op.drop_table('device_host_status')
    op.drop_table('device_network_ranges')
