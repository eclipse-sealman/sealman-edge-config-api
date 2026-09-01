"""Add extensions (dynamic API extension system)

Revision ID: a4b5c6d7e8f9
Revises: e1a2b3c4d5f6
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = 'e1a2b3c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extensions',
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('upstreams', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('internal_key_hash', sa.Text(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )
    op.create_table(
        'extension_routes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('extension_name', sa.Text(), nullable=False),
        sa.Column('upstream', sa.Text(), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False, server_default='GET'),
        sa.Column('upstream_path', sa.Text(), nullable=True),
        sa.Column('method_name', sa.Text(), nullable=True),
        sa.Column('iotedge_operation', sa.Text(), nullable=False, server_default='direct_method'),
        sa.Column('required_action', sa.Text(), nullable=True),
        sa.Column('visibility', sa.Text(), nullable=False, server_default='public'),
        sa.Column('scoped', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('scope_param', sa.Text(), nullable=False, server_default='device_id'),
        sa.Column('scope_in', sa.Text(), nullable=False, server_default='query'),
        sa.Column('query_params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('body_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['extension_name'], ['extensions.name'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['required_action'], ['actions.name']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_extension_routes_extension_name'), 'extension_routes', ['extension_name'])
    op.create_table(
        'extension_actions',
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('extension_name', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['action'], ['actions.name'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['extension_name'], ['extensions.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('action', 'extension_name'),
    )
    op.create_table(
        'extension_device_keys',
        sa.Column('extension_name', sa.Text(), nullable=False),
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('module_id', sa.Text(), nullable=True),
        sa.Column('key_hash', sa.Text(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['extension_name'], ['extensions.name'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('extension_name', 'device_id'),
    )


def downgrade() -> None:
    op.drop_table('extension_device_keys')
    op.drop_table('extension_actions')
    op.drop_index(op.f('ix_extension_routes_extension_name'), table_name='extension_routes')
    op.drop_table('extension_routes')
    op.drop_table('extensions')
