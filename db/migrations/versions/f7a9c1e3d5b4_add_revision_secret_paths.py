"""add revision secret paths

Revision ID: f7a9c1e3d5b4
Revises: e6f8b0d2c4a3
Create Date: 2026-11-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f7a9c1e3d5b4'
down_revision: Union[str, None] = 'e6f8b0d2c4a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The JSON pointers of the revision's content whose value came from a secret file, with the file's owner,
    # field, name and sha256. Existing revisions have none.
    op.add_column(
        'module_config_revisions',
        sa.Column('secret_paths', postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column('module_config_revisions', 'secret_paths')
