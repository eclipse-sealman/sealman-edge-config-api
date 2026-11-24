"""group module config templates into template groups

Revision ID: b8d4f1a6c2e5
Revises: a7c3e9d2b1f4
Create Date: 2026-11-11

Every template now belongs to a group that holds the module names (and optional scopes).
Existing templates are moved into one group per distinct module list, so behaviour is unchanged.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "b8d4f1a6c2e5"
down_revision: Union[str, None] = "a7c3e9d2b1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "module_config_template_groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("module_names", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "module_config_template_group_scopes",
        sa.Column("group_id", sa.Integer(),
                  sa.ForeignKey("module_config_template_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scope_id", UUID(as_uuid=True), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True),
    )
    op.add_column("module_config_templates", sa.Column("group_id", sa.Integer(), nullable=True))

    connection = op.get_bind()
    groups = {}
    for template_id, module_names in connection.execute(sa.text("select id, module_names from module_config_templates")).all():
        modules = sorted(module_names or [])
        key = tuple(modules)
        if key not in groups:
            name = "Default - " + ", ".join(modules) if modules else "Default"
            groups[key] = connection.execute(
                sa.text("insert into module_config_template_groups (name, module_names) values (:name, cast(:modules as jsonb)) returning id"),
                {"name": name, "modules": json.dumps(modules)},
            ).scalar_one()
        connection.execute(sa.text("update module_config_templates set group_id = :ns where id = :id"),
                           {"ns": groups[key], "id": template_id})

    op.alter_column("module_config_templates", "group_id", nullable=False)
    op.create_foreign_key("fk_module_config_templates_group", "module_config_templates", "module_config_template_groups",
                          ["group_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_module_config_templates_group", "module_config_templates", ["group_id"])
    op.drop_column("module_config_templates", "module_names")


def downgrade() -> None:
    op.add_column("module_config_templates",
                  sa.Column("module_names", JSONB(), nullable=False, server_default="[]"))
    op.execute(sa.text(
        "update module_config_templates t set module_names = n.module_names "
        "from module_config_template_groups n where n.id = t.group_id"))
    op.drop_index("ix_module_config_templates_group", table_name="module_config_templates")
    op.drop_constraint("fk_module_config_templates_group", "module_config_templates", type_="foreignkey")
    op.drop_column("module_config_templates", "group_id")
    op.drop_table("module_config_template_group_scopes")
    op.drop_table("module_config_template_groups")
