"""add extensions.endpoint_fields and endpoint_types.managed_by

Revision ID: a7b8c9d0e1f2
Revises: e5f6a7b8c9d0
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # {field_key: field definition} declared by the extension's manifest.
    op.add_column(
        "extensions",
        sa.Column("endpoint_fields", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    # {field_key: extension name} for the fields of a type that an extension manages.
    op.add_column(
        "endpoint_types",
        sa.Column("managed_by", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("endpoint_types", "managed_by")
    op.drop_column("extensions", "endpoint_fields")
