"""add module config templates and revisions

Revision ID: a7c3e9d2b1f4
Revises: c8d2e4f6a1b3
Create Date: 2026-11-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a7c3e9d2b1f4"
down_revision: Union[str, None] = "c8d2e4f6a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "module_config_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("kind", sa.Text(), nullable=False, server_default="template"),
        sa.Column("module_names", JSONB(), nullable=False, server_default="[]"),
        sa.Column("derived_from_template_id", sa.Integer(),
                  sa.ForeignKey("module_config_templates.id", ondelete="SET NULL")),
        sa.Column("derived_from_version", sa.Integer()),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "module_config_template_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("template_id", sa.Integer(),
                  sa.ForeignKey("module_config_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", JSONB(), nullable=False),
        sa.Column("variables", JSONB(), nullable=False, server_default="[]"),
        sa.Column("comment", sa.Text()),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_module_config_template_version"),
    )
    op.create_table(
        "module_config_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("module_name", sa.Text(), nullable=False),
        sa.Column("config_id", sa.Text(), nullable=False),
        sa.Column("blob_name", sa.Text(), nullable=False),
        sa.Column("parent_revision_id", sa.Integer(),
                  sa.ForeignKey("module_config_revisions.id", ondelete="SET NULL")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("template_id", sa.Integer(),
                  sa.ForeignKey("module_config_templates.id", ondelete="SET NULL")),
        sa.Column("values", JSONB()),
        sa.Column("content_hash", sa.Text()),
        sa.Column("actor", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("reapplied_from_id", sa.Integer(),
                  sa.ForeignKey("module_config_revisions.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("device_id", "module_name", "config_id", name="uq_module_config_revision_config_id"),
    )
    op.create_index("ix_module_config_revisions_device_module", "module_config_revisions",
                    ["device_id", "module_name", "id"])


def downgrade() -> None:
    op.drop_index("ix_module_config_revisions_device_module", table_name="module_config_revisions")
    op.drop_table("module_config_revisions")
    op.drop_table("module_config_template_versions")
    op.drop_table("module_config_templates")
