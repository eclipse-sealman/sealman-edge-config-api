"""add extension device keys

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-10-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extension_device_keys",
        sa.Column("extension_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("module_id", sa.Text(), nullable=True),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["extension_id"], ["extensions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("extension_id", "device_id"),
    )
    op.create_index("ix_extension_device_keys_key_hash", "extension_device_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_index("ix_extension_device_keys_key_hash", table_name="extension_device_keys")
    op.drop_table("extension_device_keys")
