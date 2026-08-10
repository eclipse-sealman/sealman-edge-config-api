"""unlock built-in ip/port fields

Revision ID: 8d6f89ad77c1
Revises: b7e2f1a4c9d3
Create Date: 2026-08-10

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '8d6f89ad77c1'
down_revision: Union[str, None] = 'b7e2f1a4c9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The built-in "ip"/"port" fields (db/sqlalchemy/endpoint.py, db/sqlalchemy/service.py) used to
# be permanently locked (`changeable: false`) once a value was saved - now they're only locked in
# the UI while assigning a type to something actually detected on the network, not as a hard
# backend rule. Existing endpoint_types/service_types rows created before this change still have
# `changeable: false` baked into their stored `fields` JSON, so it's corrected here too - the
# Python-level defaults alone only affect types created/updated from now on.
_ENDPOINT_TYPES_SQL = text(
    """
    UPDATE endpoint_types
    SET fields = jsonb_set(fields, '{ip,changeable}', 'true'::jsonb, false)
    WHERE fields ? 'ip' AND fields->'ip'->>'changeable' = 'false'
    """
)
_SERVICE_TYPES_SQL = text(
    """
    UPDATE service_types
    SET fields = jsonb_set(fields, '{port,changeable}', 'true'::jsonb, false)
    WHERE fields ? 'port' AND fields->'port'->>'changeable' = 'false'
    """
)
_ENDPOINT_TYPES_DOWNGRADE_SQL = text(
    """
    UPDATE endpoint_types
    SET fields = jsonb_set(fields, '{ip,changeable}', 'false'::jsonb, false)
    WHERE fields ? 'ip'
    """
)
_SERVICE_TYPES_DOWNGRADE_SQL = text(
    """
    UPDATE service_types
    SET fields = jsonb_set(fields, '{port,changeable}', 'false'::jsonb, false)
    WHERE fields ? 'port'
    """
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(_ENDPOINT_TYPES_SQL)
    conn.execute(_SERVICE_TYPES_SQL)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(_ENDPOINT_TYPES_DOWNGRADE_SQL)
    conn.execute(_SERVICE_TYPES_DOWNGRADE_SQL)
