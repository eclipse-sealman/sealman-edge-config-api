"""add extensions (dynamic API extension system)

Revision ID: b3c4d5e6f7a8
Revises: e1a2b3c4d5f6
Create Date: 2026-09-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'e1a2b3c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extensions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('internal_key_hash', sa.Text(), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'extension_upstreams',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('extension_id', sa.UUID(), nullable=False),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('base_url', sa.Text(), nullable=True),
        sa.Column('health_path', sa.Text(), nullable=True, server_default='/health'),
        sa.Column('version_field', sa.Text(), nullable=True, server_default='version'),
        sa.Column('expected_version', sa.Text(), nullable=True),
        sa.Column('module_name', sa.Text(), nullable=True),
        sa.Column('health_device_query', sa.Text(), nullable=True),
        sa.Column('last_checked_at', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('last_status', sa.Text(), nullable=False, server_default='unknown'),
        sa.Column('last_detail', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['extension_id'], ['extensions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_extension_upstreams_extension_id'), 'extension_upstreams', ['extension_id']
    )
    op.create_table(
        'extension_routes',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('extension_id', sa.UUID(), nullable=False),
        sa.Column('upstream_id', sa.UUID(), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False, server_default='GET'),
        sa.Column('visibility', sa.Text(), nullable=False, server_default='public'),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('deprecated', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status_code', sa.Integer(), nullable=False, server_default='200'),
        sa.Column('query_params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('example', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('validation_mode', sa.Text(), nullable=True),
        sa.Column('required_action', sa.Text(), nullable=True),
        sa.Column('scoped', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('scope_param', sa.Text(), nullable=False, server_default='device_name'),
        sa.Column('scope_in', sa.Text(), nullable=False, server_default='query'),
        sa.Column('upstream_path', sa.Text(), nullable=True),
        sa.Column('iotedge_operation', sa.Text(), nullable=True),
        sa.Column('method_name', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['extension_id'], ['extensions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['upstream_id'], ['extension_upstreams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['required_action'], ['actions.name']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_extension_routes_extension_id'), 'extension_routes', ['extension_id'])
    op.create_table(
        'extension_actions',
        sa.Column('action_name', sa.Text(), nullable=False),
        sa.Column('extension_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['action_name'], ['actions.name'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['extension_id'], ['extensions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('action_name', 'extension_id'),
    )


def downgrade() -> None:
    op.drop_table('extension_actions')
    op.drop_index(op.f('ix_extension_routes_extension_id'), table_name='extension_routes')
    op.drop_table('extension_routes')
    op.drop_index(op.f('ix_extension_upstreams_extension_id'), table_name='extension_upstreams')
    op.drop_table('extension_upstreams')
    op.drop_table('extensions')
