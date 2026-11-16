"""empty message

Revision ID: a59e7e8cb54e
Revises: 9c4e7a2b6d81, f6a7b8c9d0e1
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a59e7e8cb54e'
down_revision: Union[str, None] = ('9c4e7a2b6d81', 'f6a7b8c9d0e1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
