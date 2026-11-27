"""field files: template and module owners

Revision ID: a8b0c2d4e6f7
Revises: f7a9c1e3d5b4
Create Date: 2026-11-25

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a8b0c2d4e6f7'
down_revision: Union[str, None] = 'f7a9c1e3d5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # File variables: the default file of a template and the files of a module's applied values.
    op.drop_constraint('field_files_owner_kind', 'field_files', type_='check')
    op.create_check_constraint(
        'field_files_owner_kind', 'field_files',
        "owner_kind IN ('endpoint', 'service', 'device', 'template', 'module')")


def downgrade() -> None:
    op.execute("DELETE FROM field_files WHERE owner_kind IN ('template', 'module')")
    op.drop_constraint('field_files_owner_kind', 'field_files', type_='check')
    op.create_check_constraint(
        'field_files_owner_kind', 'field_files', "owner_kind IN ('endpoint', 'service', 'device')")
