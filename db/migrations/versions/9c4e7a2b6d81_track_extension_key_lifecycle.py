"""track extension key lifecycle

Revision ID: 9c4e7a2b6d81
Revises: f1a2b3c4d5e6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "9c4e7a2b6d81"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing internal-key issuance/rotation dates are unknown.
    op.add_column("extensions", sa.Column("internal_key_created_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("extensions", sa.Column("internal_key_rotated_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(
        "extension_device_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
    )
    op.add_column("extension_device_keys", sa.Column("label", sa.Text(), nullable=True))
    op.add_column("extension_device_keys", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("extension_device_keys", sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.drop_constraint("extension_device_keys_pkey", "extension_device_keys", type_="primary")
    op.create_primary_key("extension_device_keys_pkey", "extension_device_keys", ["id"])
    op.create_index(
        "uq_extension_device_keys_active", "extension_device_keys",
        ["extension_id", "device_id"], unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # The previous schema can retain only the current key, not revoked history.
    op.execute("DELETE FROM extension_device_keys WHERE revoked_at IS NOT NULL")
    op.drop_index("uq_extension_device_keys_active", table_name="extension_device_keys")
    op.drop_constraint("extension_device_keys_pkey", "extension_device_keys", type_="primary")
    op.create_primary_key("extension_device_keys_pkey", "extension_device_keys", ["extension_id", "device_id"])
    for column in ("revoked_at", "description", "label", "id"):
        op.drop_column("extension_device_keys", column)
    op.drop_column("extensions", "internal_key_rotated_at")
    op.drop_column("extensions", "internal_key_created_at")
