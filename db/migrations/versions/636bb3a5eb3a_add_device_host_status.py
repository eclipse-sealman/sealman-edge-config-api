"""add device host and port status history

Revision ID: 636bb3a5eb3a
Revises: 15b3bd023839
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '636bb3a5eb3a'
down_revision: Union[str, None] = '15b3bd023839'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
