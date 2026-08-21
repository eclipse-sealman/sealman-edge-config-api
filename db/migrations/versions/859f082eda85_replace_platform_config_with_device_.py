"""replace platform config with device template tables

Revision ID: 859f082eda85
Revises: 74525cffd05c, d33b34e890bc, 9b11c9dfe633
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '859f082eda85'
down_revision: Union[str, None] = ('74525cffd05c', 'd33b34e890bc', '9b11c9dfe633')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_template_variables",
        sa.Column("variable_name", sa.Text(), primary_key=True),
        sa.Column("variable_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "selected_device_templates",
        sa.Column("template_name", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.drop_table("platform_config")


def downgrade() -> None:
    op.create_table(
        "platform_config",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("device_template_config", JSONB(), nullable=False, server_default="{}"),
        sa.Column("endpoint_types", JSONB(), nullable=False, server_default="[]"),
        sa.Column("service_ports", JSONB(), nullable=False, server_default="[]"),
        sa.Column("selected_templates", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.drop_table("selected_device_templates")
    op.drop_table("device_template_variables")
