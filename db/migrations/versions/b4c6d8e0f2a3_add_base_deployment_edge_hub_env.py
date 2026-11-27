"""add edgeHub environment to the base deployment

Revision ID: b4c6d8e0f2a3
Revises: a3b5c7d9e1f2
Create Date: 2026-11-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4c6d8e0f2a3'
down_revision: Union[str, None] = 'a3b5c7d9e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows keep what the shipped manifest template sets.
    op.add_column(
        'base_deployment',
        sa.Column(
            'edge_hub_env',
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(
                '\'{"MaxUpstreamBatchSize": "100", "RuntimeLogLevel": "warning", "UpstreamProtocol": "AmqpWs"}\'::jsonb'
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column('base_deployment', 'edge_hub_env')
