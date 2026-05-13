"""add compose deployment

Revision ID: c3fd04c560ae
Revises: c4d5e6f7a8b9
Create Date: 2026-04-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'c3fd04c560ae'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compose_deployments",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("sems_compose", JSONB(), nullable=False),
        sa.Column("exposed_ports", JSONB(), nullable=False),
        sa.Column("landing_page", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    op.create_table(
        "active_deployment",
        sa.Column(
            "id",
            sa.Text(),
            primary_key=True,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "deployment_name",
            sa.Text(),
            sa.ForeignKey(
                "compose_deployments.name",
                ondelete="CASCADE"
            ),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "id = 'active'",
            name="only_one_active_row"
        ),
    )


def downgrade() -> None:
    op.drop_table("active_deployment")
    op.drop_table("compose_deployments")