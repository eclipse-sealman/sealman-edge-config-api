"""add services and endpoints

Revision ID: 74525cffd05c
Revises: c3fd04c560ae
Create Date: 2026-05-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '74525cffd05c'
down_revision: Union[str, None] = 'c3fd04c560ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('endpoint_types',
    sa.Column('type_id', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('type_id'),
    sa.UniqueConstraint('label', name='uq_endpoint_types_label')
    )
    op.create_table('service_types',
    sa.Column('type_id', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('browser_kind', sa.Text(), nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('type_id'),
    sa.UniqueConstraint('label', name='uq_service_types_label')
    )
    op.create_table('endpoints',
    sa.Column('endpoint_id', sa.Text(), nullable=False),
    sa.Column('device_id', sa.Text(), nullable=False),
    sa.Column('type_id', sa.Text(), nullable=False),
    sa.Column('endpoint_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['type_id'], ['endpoint_types.type_id'], ),
    sa.PrimaryKeyConstraint('endpoint_id')
    )
    op.create_table('services',
    sa.Column('service_id', sa.Text(), nullable=False),
    sa.Column('endpoint_id', sa.Text(), nullable=False),
    sa.Column('type_id', sa.Text(), nullable=False),
    sa.Column('service_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['endpoint_id'], ['endpoints.endpoint_id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['type_id'], ['service_types.type_id'], ),
    sa.PrimaryKeyConstraint('service_id')
    )
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
    op.drop_table('services')
    op.drop_table('endpoints')
    op.drop_table('service_types')
    op.drop_table('endpoint_types')
