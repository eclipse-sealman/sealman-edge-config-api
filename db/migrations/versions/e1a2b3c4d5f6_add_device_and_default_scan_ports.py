"""add device and default scan ports

Revision ID: e1a2b3c4d5f6
Revises: 8d6f89ad77c1
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'e1a2b3c4d5f6'
down_revision: Union[str, None] = '8d6f89ad77c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'device_scan_ports',
        sa.Column('device_id', sa.Text(), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('device_id', 'port'),
    )
    op.create_table(
        'default_scan_ports',
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('port'),
    )

    # Seed from every service type's currently configured default port - this is exactly what
    # get_network_scan_ports.py used to compute live on every call, so switching it over to read
    # this persisted table instead doesn't change what's actually scanned for any existing
    # installation.
    conn = op.get_bind()
    conn.execute(text(
        """
        INSERT INTO default_scan_ports (port)
        SELECT DISTINCT (fields->'port'->>'default')::integer
        FROM service_types
        WHERE fields->'port'->>'default' IS NOT NULL
        ON CONFLICT (port) DO NOTHING
        """
    ))


def downgrade() -> None:
    op.drop_table('default_scan_ports')
    op.drop_table('device_scan_ports')
