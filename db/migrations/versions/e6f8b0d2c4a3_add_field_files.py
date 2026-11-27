"""add field files

Revision ID: e6f8b0d2c4a3
Revises: d5e7a9c1b3f2
Create Date: 2026-11-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e6f8b0d2c4a3'
down_revision: Union[str, None] = 'd5e7a9c1b3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The owner is an endpoint, service or device row, so there is no foreign key.
    # The unique constraint also serves the lookups by owner. `content` is never indexed.
    op.create_table(
        'field_files',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('owner_kind', sa.Text(), nullable=False),
        sa.Column('owner_id', sa.Text(), nullable=False),
        sa.Column('field', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('secret', sa.Boolean(), nullable=False),
        sa.Column('sha256', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('subject', sa.Text(), nullable=True),
        sa.Column('not_after', postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('uploaded_by', sa.Text(), nullable=False),
        sa.Column('uploaded_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_kind', 'owner_id', 'field', name='field_files_owner_field'),
        sa.CheckConstraint("owner_kind IN ('endpoint', 'service', 'device')", name='field_files_owner_kind'),
    )
    # Encrypted content does not compress: store it out-of-line (TOAST) without another compression pass.
    op.execute("ALTER TABLE field_files ALTER COLUMN content SET STORAGE EXTERNAL")


def downgrade() -> None:
    op.drop_table('field_files')
