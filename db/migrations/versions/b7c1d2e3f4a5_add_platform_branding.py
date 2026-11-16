"""add platform branding

Revision ID: b7c1d2e3f4a5
Revises: 71baa0308111
Create Date: 2026-11-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, None] = '71baa0308111'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'platform_branding',
        sa.Column('id', sa.SmallInteger(), nullable=False),
        sa.Column('theme_id', sa.Text(), nullable=False),
        sa.Column('variables', postgresql.JSONB(), nullable=False),
        sa.Column('app_title', sa.Text(), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('id = 1', name='platform_branding_singleton'),
    )
    # At most two rows and only the primary key is indexed - the image bytes are never indexed.
    op.create_table(
        'platform_branding_assets',
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('content_type', sa.Text(), nullable=False),
        sa.Column('etag', sa.Text(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('kind'),
        sa.CheckConstraint("kind IN ('logo', 'favicon')", name='platform_branding_assets_kind'),
    )
    # Images are already compressed: store them out-of-line (TOAST) without another compression pass.
    op.execute("ALTER TABLE platform_branding_assets ALTER COLUMN content SET STORAGE EXTERNAL")


def downgrade() -> None:
    op.drop_table('platform_branding_assets')
    op.drop_table('platform_branding')
