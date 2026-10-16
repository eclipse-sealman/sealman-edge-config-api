"""add extension_module_schemas and extension_routes response_ref/response_schema

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extension_module_schemas',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('extension_id', sa.UUID(), nullable=False),
        sa.Column('module_id', sa.Text(), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('upstream', sa.Text(), nullable=False),
        sa.Column('schema_ref', sa.Text(), nullable=False),
        sa.Column('schema', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('version', sa.Text(), nullable=False),
        sa.Column('resolved_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint("purpose IN ('configuration', 'reported')", name='ck_extension_module_schemas_purpose'),
        sa.ForeignKeyConstraint(['extension_id'], ['extensions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_id', 'purpose', name='uq_extension_module_schemas_module_purpose'),
    )
    op.create_index(
        op.f('ix_extension_module_schemas_extension_id'),
        'extension_module_schemas',
        ['extension_id'],
        unique=False,
    )
    op.add_column(
        'extension_routes',
        sa.Column('response_ref', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'extension_routes',
        sa.Column('response_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('extension_routes', 'response_schema')
    op.drop_column('extension_routes', 'response_ref')
    op.drop_index(op.f('ix_extension_module_schemas_extension_id'), table_name='extension_module_schemas')
    op.drop_table('extension_module_schemas')
