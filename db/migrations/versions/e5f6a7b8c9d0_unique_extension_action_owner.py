"""make extension action ownership unique

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            """
            SELECT action_name
            FROM extension_actions
            GROUP BY action_name
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise RuntimeError(
            f"Cannot enforce unique extension action ownership: '{duplicate}' has multiple owners"
        )
    op.create_unique_constraint(
        "uq_extension_actions_action_name",
        "extension_actions",
        ["action_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_extension_actions_action_name",
        "extension_actions",
        type_="unique",
    )