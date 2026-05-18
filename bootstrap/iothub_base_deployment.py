"""
bootstrap/iothub_base_deployment.py — Async IoTHub Base Deployment Bootstrap
==============================================================================
Idempotent creation of the 'seal-base-deployment' automatic IoTHub deployment.
Called once at API startup when BOOTSTRAP_ENABLED=true.

Bootstrap flow
--------------
  1. GET the configuration — if it already exists (HTTP 200), skip.
  2. If absent (HTTP 404), PATCH the full deployment body to create it.
  3. Any other error is logged and startup continues (non-fatal).
"""

import json
import logging
from pathlib import Path
from string import Template

from async_requests import get_async, put_async
from constants import (
    IOT_HUB_NAME,
    CONTAINER_REGISTRY_ADDRESS,
    CONTAINER_REGISTRY_USERNAME,
    CONTAINER_REGISTRY_PASSWORD,
    EDGE_AGENT_IMAGE,
    EDGE_HUB_IMAGE,
    BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
    BOOTSTRAP_IOTHUB_TARGET_CONDITION,
    BOOTSTRAP_IOTHUB_PRIORITY,
)
from helper import get_iothub_auth_headers

_log = logging.getLogger("IoTHubBootstrap")
_log.setLevel(logging.INFO)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(logging.Formatter(
    fmt="%(levelname)s:     %(asctime)s >> %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
_log.addHandler(_log_handler)

_MANIFEST_FILE = Path(__file__).parent / "seal-base-deployment.json"
_IOTHUB_API_VERSION = "2024-03-31"


def _load_deployment_body() -> dict:
    raw = _MANIFEST_FILE.read_text()
    rendered = Template(raw).substitute(
        deployment_name=BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
        target_condition=BOOTSTRAP_IOTHUB_TARGET_CONDITION,
        priority=BOOTSTRAP_IOTHUB_PRIORITY,
        container_registry_address=CONTAINER_REGISTRY_ADDRESS,
        container_registry_username=CONTAINER_REGISTRY_USERNAME,
        container_registry_password=CONTAINER_REGISTRY_PASSWORD,
        edge_agent_image=EDGE_AGENT_IMAGE,
        edge_hub_image=EDGE_HUB_IMAGE,
    )
    return json.loads(rendered)


async def bootstrap_iothub_base_deployment() -> None:
    """
    Idempotently create the base IoTHub automatic deployment.
    Called once at API startup.
    """
    _log.info("═══ IoTHub Base Deployment Bootstrap — starting ═══")

    if not IOT_HUB_NAME:
        _log.warning("IOT_HUB_NAME is not set — skipping IoTHub bootstrap")
        _log.warning("═══ IoTHub Base Deployment Bootstrap — skipped (no IOT_HUB_NAME) ═══")
        return

    url = f"https://{IOT_HUB_NAME}/configurations/{BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME}?api-version={_IOTHUB_API_VERSION}"
    headers = get_iothub_auth_headers()

    try:
        resp = {}
        await get_async(url, resp, headers=headers, timeout=10)
        status = resp[url].status_code

        if status == 200:
            _log.info(
                "Deployment '%s' already exists — skipping creation",
                BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
            )
            _log.info("═══ IoTHub Base Deployment Bootstrap — done (already provisioned) ═══")
            return

        if status != 404:
            _log.error(
                "Unexpected HTTP %d when checking deployment '%s' — skipping",
                status,
                BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
            )
            _log.warning("═══ IoTHub Base Deployment Bootstrap — skipped (unexpected response) ═══")
            return

        _log.info("Deployment '%s' not found — creating…", BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME)
        body = _load_deployment_body()

        put_resp = {}
        await put_async(url, put_resp, _json=body, headers=headers, timeout=15)
        put_status = put_resp[url].status_code

        if put_status in (200, 201):
            _log.info(
                "Deployment '%s' created successfully (HTTP %d)",
                BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
                put_status,
            )
        else:
            _log.error(
                "Failed to create deployment '%s': HTTP %d — %s",
                BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
                put_status,
                put_resp[url].text,
            )

    except Exception as ex:
        _log.error(
            "IoTHub bootstrap encountered an unexpected error — continuing startup: %s",
            ex,
            exc_info=True,
        )

    _log.info("═══ IoTHub Base Deployment Bootstrap — done ═══")
