"""seed default service types

Revision ID: b7e2f1a4c9d3
Revises: 859f082eda85
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'b7e2f1a4c9d3'
down_revision: Union[str, None] = '859f082eda85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed, hand-picked UUIDs (not generated at migration run time) so this seed data is
# deterministic and safe to reference/re-run against - matches the fixed 'default' type_id used
# for the seed device type in a1b2c3d4e5f6_initial_schema.py.
_SERVICE_TYPES = [
    {
        "type_id": "96e3f9dc-0be4-4e80-b726-4bb4277f5c6e",
        "label": "FTP",
        "description": "File Transfer Protocol",
        "port": 21,
        "browser_kind": None,
    },
    {
        "type_id": "541b6464-c036-4f3b-859a-af8423690f45",
        "label": "OPC UA",
        "description": "OPC Unified Architecture",
        "port": 4840,
        # Matches the "opcua" kind registered in src/lib/browsers/registerBrowsers.tsx.
        "browser_kind": "opcua",
    },
    {
        "type_id": "fae704b8-0c65-4754-a937-e8fe52a747a5",
        "label": "Web VNC",
        "description": "VNC remote desktop",
        "port": 5900,
        # No "vnc" browser is registered on the frontend yet - leave unset rather than pointing
        # the "Browse" button at a kind that doesn't resolve to anything.
        "browser_kind": None,
    },
    {
        "type_id": "b0d02ca8-562d-4a7f-9b9b-03b5b550f653",
        "label": "MQTT",
        "description": "MQTT message broker",
        "port": 1883,
        "browser_kind": None,
    },
    {
        "type_id": "e67d21bd-6155-4dca-8989-3164343d7dd5",
        "label": "Landing Page",
        "description": "Device web landing page",
        "port": 9090,
        "browser_kind": None,
    },
]


_INSERT_SQL = text(
    """
    INSERT INTO service_types (type_id, label, description, fields, mapping, browser_kind)
    VALUES (
        :type_id,
        :label,
        :description,
        jsonb_build_object(
            'port', jsonb_build_object(
                'type', 'integer',
                'label', 'Port',
                'required', true,
                'changeable', false,
                'show_in_list', false,
                'ui', 'number',
                'default', :port
            )
        ),
        '{"port": "port"}'::jsonb,
        :browser_kind
    )
    """
)

_DELETE_SQL = text("DELETE FROM service_types WHERE type_id = :type_id")


def upgrade() -> None:
    conn = op.get_bind()
    # Every service type always has this built-in "port" field (see
    # db/sqlalchemy/service.py:_PORT_FIELD_DEFINITION) - required, non-changeable, mapped to the
    # "port" role, seeded here with this type's well-known default port.
    for service_type in _SERVICE_TYPES:
        conn.execute(_INSERT_SQL, service_type)


def downgrade() -> None:
    conn = op.get_bind()
    for service_type in _SERVICE_TYPES:
        conn.execute(_DELETE_SQL, {"type_id": service_type["type_id"]})
