"""move container registry variables to container_registry_auths and seed static compose stacks

Revision ID: f2a4c6e8b0d1
Revises: e7f8a9b0c1d2
Create Date: 2026-11-26

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a4c6e8b0d1'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AUTHS = "container_registry_auths"
COMPOSE = "static_compose_files"
LEGACY_ADDRESS = "container_registry_address"
LEGACY_CREDENTIALS = "container_registry_credentials_encoded"

# The stack that used to be hardcoded in the SEMS Twig template.
CONNECTION_CHECK_YAML = """version: "3.8"

services:
  backend:
    image: {registry}/seal-app-connection-check-backend:1.0.1
    container_name: seal-app-conn-check-api
    restart: unless-stopped
    ports:
      - "9091:9091"
    networks:
      - "local-network"
    environment:
      - FLASK_APP=main.py
      - FLASK_RUN_HOST=0.0.0.0
      - FLASK_RUN_PORT=9091
    volumes:
      - /run/mgmtd:/run/mgmtd
    tty: true
    stdin_open: true

  frontend:
    image: {registry}/seal-app-connection-check-frontend:1.0.1
    container_name: seal-app-conn-check-ui
    ports:
      - "9090:9090"
    networks:
      - "local-network"
    restart: unless-stopped

networks:
  local-network:
    driver: "bridge"
"""


def _upsert(conn, name: str, value: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO device_template_variables (variable_name, variable_value) "
            "VALUES (:n, :v) ON CONFLICT (variable_name) DO UPDATE SET variable_value = EXCLUDED.variable_value"
        ),
        {"n": name, "v": value},
    )


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT variable_name, variable_value FROM device_template_variables WHERE variable_name IN (:a, :c)"),
        {"a": LEGACY_ADDRESS, "c": LEGACY_CREDENTIALS},
    ).all()
    legacy = {name: value for name, value in rows}
    address = legacy.get(LEGACY_ADDRESS) or "docker.io"
    credentials = legacy.get(LEGACY_CREDENTIALS)

    if credentials or legacy.get(LEGACY_ADDRESS):
        _upsert(conn, AUTHS, json.dumps({address: {"auth": credentials or None}}))
    _upsert(
        conn,
        COMPOSE,
        json.dumps({"seal-app-connection-check": CONNECTION_CHECK_YAML.format(registry=address)}),
    )
    conn.execute(
        sa.text("DELETE FROM device_template_variables WHERE variable_name IN (:a, :c)"),
        {"a": LEGACY_ADDRESS, "c": LEGACY_CREDENTIALS},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM device_template_variables WHERE variable_name IN (:a, :c)"),
        {"a": AUTHS, "c": COMPOSE},
    )
