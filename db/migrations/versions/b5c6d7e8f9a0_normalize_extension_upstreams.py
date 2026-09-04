"""Normalize extension upstreams table

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extension_upstreams',
        sa.Column('extension_name', sa.Text(), nullable=False),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('base_url', sa.Text(), nullable=True),
        sa.Column('module_name', sa.Text(), nullable=True),
        sa.Column('expected_version', sa.Text(), nullable=True),
        sa.Column('health_path', sa.Text(), nullable=True),
        sa.Column('version_field', sa.Text(), nullable=True),
        sa.Column('version_source', sa.Text(), nullable=True),
        sa.Column('twin_version_property', sa.Text(), nullable=True),
        sa.Column('health_method', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['extension_name'], ['extensions.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('extension_name', 'key'),
    )

    # Migrate any JSONB upstreams if records exist
    op.execute(
        """
        INSERT INTO extension_upstreams (
            extension_name, key, type, base_url, module_name,
            expected_version, health_path, version_field,
            version_source, twin_version_property, health_method
        )
        SELECT
            e.name,
            u.key,
            COALESCE(u.value->>'type', 'http'),
            u.value->>'base_url',
            u.value->>'module_name',
            u.value->>'expected_version',
            u.value->>'health_path',
            u.value->>'version_field',
            u.value->>'version_source',
            u.value->>'twin_version_property',
            u.value->>'health_method'
        FROM extensions e,
        jsonb_each(e.upstreams) AS u
        ON CONFLICT DO NOTHING
        """
    )

    op.drop_column('extensions', 'upstreams')

    op.alter_column('extension_routes', 'upstream', new_column_name='upstream_name')

    op.create_foreign_key(
        'fk_extension_routes_upstream',
        'extension_routes',
        'extension_upstreams',
        ['extension_name', 'upstream_name'],
        ['extension_name', 'key'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint('fk_extension_routes_upstream', 'extension_routes', type_='foreignkey')
    op.alter_column('extension_routes', 'upstream_name', new_column_name='upstream')
    op.add_column('extensions', sa.Column('upstreams', sa.dialects.postgresql.JSONB(), server_default='{}', nullable=False))
    op.drop_table('extension_upstreams')
