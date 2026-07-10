"""add_platform_config_columns

Revision ID: 9b11c9dfe633
Revises: aff23bbdb5bd
Create Date: 2026-07-09

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '9b11c9dfe633'
down_revision: Union[str, None] = 'aff23bbdb5bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform",
        sa.Column("endpoint_types", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "platform",
        sa.Column("service_ports", JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "platform",
        sa.Column("selected_templates", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("platform", "selected_templates")
    op.drop_column("platform", "service_ports")
    op.drop_column("platform", "endpoint_types")
