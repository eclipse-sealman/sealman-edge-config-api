#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11, <3.14"
# dependencies = [
#   "psycopg[binary]>=3.3.3",
#   "httpx>=0.27.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""
IoT Hub → PostgreSQL Device Importer
=====================================
Reads all IoT Edge devices from Azure IoT Hub and upserts them into PostgreSQL database.

Merge strategy (idempotent):
  - Device does not exist in DB → INSERT with all IoT Hub tags as device_meta.
  - Device already exists in DB → MERGE metadata: new tags are added, but tags
    already present in the DB are not overwritten or removed.
    Running the script multiple times with the same IoT Hub data always
    produces the same final state.

Required environment variables:
  IOTHUB_SAS_TOKEN            Azure IoT Hub Shared Access Signature token
                              e.g. SharedAccessSignature sr=xxx.azure-devices.net&sig=xxx&se=xxx&skn=xxx
  POSTGRES_CONNECTION_STRING  PostgreSQL DSN
                              e.g. postgresql://user:password@host:5432/dbname
"""

import argparse
import json
import logging
import os
from pathlib import Path
import urllib.parse

import httpx
import psycopg
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directory that contains this script file — used to resolve the default .env
# path so the script works correctly regardless of the working directory.
_SCRIPT_DIR = Path(__file__).parent

IOTHUB_QUERY = "SELECT * FROM devices WHERE capabilities.iotEdge = true"
PAGE_SIZE = 100

# ---------------------------------------------------------------------------
# CLI / configuration
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """
    Resolve configuration from (in order of precedence):
      1. CLI arguments
      2. Environment variables
      3. .env file (if --env-file is provided or a .env exists in the CWD)
    """
    parser = argparse.ArgumentParser(
        description="Import IoT Edge devices from Azure IoT Hub into PostgreSQL."
    )
    parser.add_argument(
        "--iothub-sas-token", "-i",
        default=os.environ.get("IOTHUB_SAS_TOKEN"),
        metavar="SAS_TOKEN",
        help="Azure IoT Hub SAS token (env: IOTHUB_SAS_TOKEN)",
    )
    parser.add_argument(
        "--postgres-connection-string", "-p",
        default=os.environ.get("POSTGRES_CONNECTION_STRING"),
        metavar="DSN",
        help="PostgreSQL DSN, e.g. postgresql://user:pass@host:5433/db (env: POSTGRES_CONNECTION_STRING)",
    )
    parser.add_argument(
        "--env-file",
        default=str(_SCRIPT_DIR / ".env"),
        metavar="PATH",
        help="Path to a .env file to load (default: .env next to the script)",
    )

    args = parser.parse_args()

    # Load .env file — variables already set in the environment take precedence
    # (override=False), so explicit env exports or CLI args always win.
    load_dotenv(args.env_file, override=False)

    # Re-read env vars after dotenv load for any values not set on the CLI.
    if not args.iothub_sas_token:
        args.iothub_sas_token = os.environ.get("IOTHUB_SAS_TOKEN", "").strip()
    if not args.postgres_connection_string:
        args.postgres_connection_string = os.environ.get("POSTGRES_CONNECTION_STRING", "").strip()

    errors = []
    if not args.iothub_sas_token:
        errors.append("  --iothub-sas-token / IOTHUB_SAS_TOKEN")
    if not args.postgres_connection_string:
        errors.append("  --postgres-connection-string / POSTGRES_CONNECTION_STRING")
    if errors:
        parser.error("Missing required configuration:\n" + "\n".join(errors))

    return args


# ---------------------------------------------------------------------------
# IoT Hub — REST API helpers
# ---------------------------------------------------------------------------

_IOTHUB_API_VERSION = "2021-04-12"


def _extract_hostname_from_sas_token(sas_token: str) -> str:
    """Extract the IoT Hub hostname from the SAS token's ``sr`` parameter.

    A SAS token has the form::

        SharedAccessSignature sr={url-encoded-hostname}&sig=...&se=...&skn=...

    The ``sr`` value is the URL-encoded resource URI, which for IoT Hub is
    just the hostname (e.g. ``myiothub.azure-devices.net``).
    """
    params = sas_token.removeprefix("SharedAccessSignature").strip()
    for part in params.split("&"):
        key, _, value = part.partition("=")
        if key == "sr":
            return urllib.parse.unquote(value)
    raise ValueError(
        "Cannot extract hostname from SAS token: 'sr' parameter not found. "
        "Expected format: SharedAccessSignature sr=xxx.azure-devices.net&sig=...&se=...&skn=..."
    )


# ---------------------------------------------------------------------------
# IoT Hub
# ---------------------------------------------------------------------------


def fetch_iot_edge_devices(sas_token: str) -> list[dict]:
    """
    Return a list of {'device_id': str, 'tags': dict} for every IoT Edge
    device twin found in the hub, handling pagination automatically.
    """
    hostname = _extract_hostname_from_sas_token(sas_token)

    url = f"https://{hostname}/devices/query"
    base_headers = {
        "Authorization": sas_token,
        "Content-Type": "application/json",
        "x-ms-max-item-count": str(PAGE_SIZE),
    }
    body = {"query": IOTHUB_QUERY}

    devices: list[dict] = []
    continuation_token: str | None = None

    logger.info("Querying IoT Edge device twins via REST API (query: %s)", IOTHUB_QUERY)
    with httpx.Client(timeout=30) as client:
        while True:
            headers = dict(base_headers)
            if continuation_token:
                headers["x-ms-continuation"] = continuation_token

            response = client.post(
                url,
                params={"api-version": _IOTHUB_API_VERSION},
                headers=headers,
                json=body,
            )
            response.raise_for_status()

            for item in response.json():
                device_id: str | None = item.get("deviceId")
                tags: dict = item.get("tags") or {}
                if device_id:
                    devices.append({"device_id": device_id, "tags": tags})

            continuation_token = response.headers.get("x-ms-continuation")
            if not continuation_token:
                break

    logger.info("Found %d IoT Edge device(s).", len(devices))
    return devices


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

# ON CONFLICT merge strategy:
#   EXCLUDED.device_meta  – the new tags coming from IoT Hub
#   devices.device_meta   – the tags already stored in the DB
#
# The || operator merges two jsonb objects; the RIGHT-HAND side wins when a
# key exists in both.  By putting `devices.device_meta` on the right we
# ensure existing DB values always take precedence over incoming IoT Hub
# values, while new keys are still added.
UPSERT_SQL = """
    INSERT INTO devices (device_id, device_meta)
    VALUES (%(device_id)s, %(device_meta)s::jsonb)
    ON CONFLICT (device_id) DO UPDATE
        SET device_meta = EXCLUDED.device_meta || devices.device_meta
"""

EXISTS_SQL = "SELECT 1 FROM devices WHERE device_id = %(device_id)s"


def fetch_allowed_meta_keys(pg_conn_str: str) -> set[str]:
    """
    Read the key names defined in platform_meta for the 'default' platform row.
    Only IoT Hub tag keys that appear here will be written into device_meta.
    """
    logger.info("Reading allowed meta keys from platform table…")
    with psycopg.connect(pg_conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT platform_meta FROM platform WHERE name = %s",
                ("default",),
            )
            row = cur.fetchone()

    if not row or row[0] is None:
        raise RuntimeError(
            "No 'default' row found in the platform table, "
            "or its platform_meta column is NULL."
        )

    # psycopg3 deserialises JSONB → dict automatically.
    keys: set[str] = set(row[0].keys())
    logger.info("Allowed meta keys (%d): %s", len(keys), sorted(keys))
    return keys


def upsert_devices(pg_conn_str: str, devices: list[dict], allowed_keys: set[str]) -> None:
    """Upsert all devices into the PostgreSQL `devices` table.

    Only tag keys present in *allowed_keys* (sourced from platform_meta) are
    written to device_meta; all other IoT Hub tags are silently dropped.
    """
    if not devices:
        logger.info("No devices to import.")
        return

    logger.info("Connecting to PostgreSQL…")
    inserted = 0
    updated = 0

    with psycopg.connect(pg_conn_str) as conn:
        with conn.cursor() as cur:
            for dev in devices:
                device_id: str = dev["device_id"]

                # Keep only the keys that are defined in platform_meta.
                raw_tags: dict = dev["tags"]
                filtered_tags = {k: v for k, v in raw_tags.items() if k in allowed_keys}
                dropped = raw_tags.keys() - allowed_keys
                if dropped:
                    logger.debug(
                        "  [%s] dropped %d tag(s) not in platform_meta: %s",
                        device_id, len(dropped), sorted(dropped),
                    )

                meta_json: str = json.dumps(filtered_tags)
                params = {"device_id": device_id, "device_meta": meta_json}

                # Determine whether the row exists so we can log accurately.
                cur.execute(EXISTS_SQL, {"device_id": device_id})
                exists = cur.fetchone() is not None

                cur.execute(UPSERT_SQL, params)

                if exists:
                    updated += 1
                    logger.info("  [updated]  %s", device_id)
                else:
                    inserted += 1
                    logger.info("  [inserted] %s", device_id)

        conn.commit()

    logger.info(
        "Import complete. Inserted: %d | Updated: %d | Total: %d",
        inserted,
        updated,
        inserted + updated,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    allowed_keys = fetch_allowed_meta_keys(args.postgres_connection_string)
    devices = fetch_iot_edge_devices(args.iothub_sas_token)
    upsert_devices(args.postgres_connection_string, devices, allowed_keys)


if __name__ == "__main__":
    main()

