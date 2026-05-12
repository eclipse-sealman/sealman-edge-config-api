"""
bootstrap/sems.py — Async SEMS Bootstrap
=========================================
Idempotent first-time provisioning of a fresh Sealman EMS instance.
Called once at API startup (before the SmartEMS token loop) when
BOOTSTRAP_ENABLED=true.

Bootstrap flow
--------------
  0. Health check: poll until SEMS responds.
  1. Fixture login (admin:admin): if it works, create the service account.
     Silently skipped when the fixture admin is already disabled or rotated —
     this is the normal case on every restart after the first run.
  2. Service-account login: authenticate for all subsequent setup work.
  3. Disable fixture admin (via the service account session, only when step 1 ran).
  4. Config + Template: find-or-create the Twig device config, template,
     and production template version.
"""

import time
import logging
from pathlib import Path
import httpx

from async_requests import post_async
from constants import (
    SEMS_URL,
    SEMS_USER,
    SEMS_PW,
    BOOTSTRAP_SEMS_CONFIG_NAME,
    BOOTSTRAP_SEMS_TEMPLATE_NAME,
    BOOTSTRAP_SEMS_DEVICE_TYPE,
    BOOTSTRAP_SEMS_HEALTH_TIMEOUT,
)

import asyncio

_log = logging.getLogger("SEMSBootstrap")
_log.setLevel(logging.INFO)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(logging.Formatter(
    fmt="%(levelname)s:     %(asctime)s >> %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
_log.addHandler(_log_handler)

_CONFIG_FILE = Path(__file__).parent / "sealman-sems-config.twig"

_FIXTURE_USER = "admin"
_FIXTURE_PASS = "admin"


def _load_config_content() -> str:
    _log.info("Loading config content from '%s'", _CONFIG_FILE)
    with open(_CONFIG_FILE) as fh:
        return fh.read()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# Step 0 — Health check
# ─────────────────────────────────────────────────────────────────────────────

async def _health_check() -> bool:
    """
    Poll until SEMS is ready. Uses exponential back-off capped at 15 s.
    Returns True when ready, False on timeout.
    """
    url = f"{SEMS_URL}/web/api/authentication/login_check"
    deadline = time.time() + BOOTSTRAP_SEMS_HEALTH_TIMEOUT
    interval = 2
    _log.info("Waiting for SEMS at %s (timeout: %d s)…", SEMS_URL, BOOTSTRAP_SEMS_HEALTH_TIMEOUT)

    while True:
        try:
            resp = {}
            await post_async(
                url, resp,
                _json={"username": "_healthcheck_", "password": "_healthcheck_"},
                timeout=5,
            )
            if resp[url].status_code != 400:
                _log.info("SEMS is ready (HTTP %d)", resp[url].status_code)
                return True
            # HTTP 400 means DB migrations are not yet complete — treat as not ready
            raise httpx.ConnectError("DB not yet seeded (HTTP 400)")
        except Exception as exc:
            if time.time() >= deadline:
                _log.error("SEMS not ready after %d s — skipping bootstrap", BOOTSTRAP_SEMS_HEALTH_TIMEOUT)
                return False
            _log.debug("Not ready yet — retrying in %d s (%s)", interval, exc)
            await asyncio.sleep(interval)
            interval = min(interval * 2, 15)


# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _login(username: str, password: str) -> str | None:
    """Return a JWT token string on success, None on bad credentials."""
    url = f"{SEMS_URL}/web/api/authentication/login_check"
    resp = {}
    await post_async(url, resp, _json={"username": username, "password": password})
    if resp[url].status_code in (400, 401):
        return None
    resp[url].raise_for_status()
    return resp[url].json().get("token")


# ─────────────────────────────────────────────────────────────────────────────
# Shared list/find helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _find_one(path: str, field: str, value: str, token: str) -> dict | None:
    """Return the first result where `field` equals `value`, or None."""
    url = f"{SEMS_URL}{path}"
    resp = {}
    await post_async(
        url, resp,
        _json={
            "page": 1,
            "rowsPerPage": 2,
            "filters": {
                field: {"filterBy": field, "filterType": "equal", "filterValue": value}
            },
        },
        headers=_auth_header(token),
    )
    resp[url].raise_for_status()
    results = resp[url].json().get("results", [])
    return results[0] if results else None


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Fixture setup
# ─────────────────────────────────────────────────────────────────────────────

async def _fixture_setup() -> bool:
    """
    Try the fixture admin credentials (admin:admin). If they work (fresh instance),
    create the service account. Returns True when fixture setup ran so the caller
    knows to disable the fixture admin afterwards.

    Note: on every restart after the first run the fixture admin is already disabled,
    so this login returns 401 immediately and the step is skipped with no side effects.
    """
    _log.info("Trying fixture credentials (%s:****)…", _FIXTURE_USER)
    token = await _login(_FIXTURE_USER, _FIXTURE_PASS)
    if token is None:
        _log.info("Fixture login failed — fixture admin already disabled or rotated, skipping")
        return False

    _log.info("Fixture login succeeded — running first-time setup")

    existing = await _find_one("/web/api/user/list", "username", SEMS_USER, token)
    if existing:
        _log.info("Service user '%s' already exists (id=%s) — skipping creation", SEMS_USER, existing["id"])
    else:
        _log.info("Creating service user '%s'…", SEMS_USER)
        url = f"{SEMS_URL}/web/api/user/create"
        resp = {}
        await post_async(
            url, resp,
            _json={
                "username": SEMS_USER,
                "plainPassword": SEMS_PW,
                "plainPasswordRepeat": SEMS_PW,
                "enabled": True,
                "roleAdmin": True,
            },
            headers=_auth_header(token),
        )
        resp[url].raise_for_status()
        _log.info("Service user '%s' created (id=%s)", SEMS_USER, resp[url].json()["id"])

    return True


async def _disable_fixture_admin(token: str) -> None:
    """Disable the fixture admin. Must be called with the service account token."""
    admin_user = await _find_one("/web/api/user/list", "username", _FIXTURE_USER, token)
    if not admin_user:
        _log.warning("Fixture admin not found in user list — cannot disable")
        return
    if not admin_user.get("enabled", True):
        _log.info("Fixture admin already disabled — skipping")
        return
    _log.info("Disabling fixture admin (id=%s)…", admin_user["id"])
    url = f"{SEMS_URL}/web/api/user/disable/{admin_user['id']}"
    resp = {}
    await post_async(url, resp, _json={}, headers=_auth_header(token))
    resp[url].raise_for_status()
    _log.info("Fixture admin disabled")


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Config + Template setup
# ─────────────────────────────────────────────────────────────────────────────

async def _ensure_config(token: str, device_type_id: int, content: str) -> int:
    existing = await _find_one("/web/api/config/list", "name", BOOTSTRAP_SEMS_CONFIG_NAME, token)
    if existing:
        _log.info("Config '%s' already exists (id=%s) — skipping creation", BOOTSTRAP_SEMS_CONFIG_NAME, existing["id"])
        return existing["id"]

    _log.info("Creating config '%s' for device type %d…", BOOTSTRAP_SEMS_CONFIG_NAME, device_type_id)
    url = f"{SEMS_URL}/web/api/config/create"
    resp = {}
    await post_async(
        url, resp,
        _json={
            "deviceType": str(device_type_id),
            "feature": "1",  # '1' = PRIMARY
            "name": BOOTSTRAP_SEMS_CONFIG_NAME,
            "generator": "twig",
            "content": content,
        },
        headers=_auth_header(token),
    )
    resp[url].raise_for_status()
    config_id = resp[url].json()["id"]
    _log.info("Config created (id=%d)", config_id)
    return config_id


async def _ensure_template(token: str, device_type_id: int) -> dict:
    existing = await _find_one("/web/api/template/list", "name", BOOTSTRAP_SEMS_TEMPLATE_NAME, token)
    if existing:
        _log.info("Template '%s' already exists (id=%s) — skipping creation", BOOTSTRAP_SEMS_TEMPLATE_NAME, existing["id"])
        return existing

    _log.info("Creating template '%s' for device type %d…", BOOTSTRAP_SEMS_TEMPLATE_NAME, device_type_id)
    url = f"{SEMS_URL}/web/api/template/create"
    resp = {}
    await post_async(
        url, resp,
        _json={"name": BOOTSTRAP_SEMS_TEMPLATE_NAME, "deviceType": str(device_type_id)},
        headers=_auth_header(token),
    )
    resp[url].raise_for_status()
    template = resp[url].json()
    _log.info("Template created (id=%d)", template["id"])
    return template


async def _push_to_production(token: str, template: dict, config_id: int) -> None:
    """
    Create a staging template version and promote it to production.
    Skips if the template already has a production version — we do not
    overwrite an existing production rollout from the bootstrap script.

    SEMS promotion sequence:
      create/staging  →  select/staging  →  select/production
    """
    if template.get("productionTemplate"):
        _log.info("Template already has a production version — skipping push to production")
        return

    template_id = template["id"]

    _log.info("Creating staging template version for template id=%d…", template_id)
    url_create = f"{SEMS_URL}/web/api/templateversion/create/staging/{template_id}"
    resp = {}
    await post_async(
        url_create, resp,
        _json={"name": "v1.0", "variables": [], "config1": str(config_id)},
        headers=_auth_header(token),
    )
    resp[url_create].raise_for_status()
    tv_id = resp[url_create].json()["id"]
    _log.info("Staging template version created (id=%d)", tv_id)

    url_staging = f"{SEMS_URL}/web/api/templateversion/select/staging/{tv_id}"
    resp2 = {}
    await post_async(url_staging, resp2, _json={}, headers=_auth_header(token))
    resp2[url_staging].raise_for_status()
    _log.info("Template version %d selected as staging", tv_id)

    url_prod = f"{SEMS_URL}/web/api/templateversion/select/production/{tv_id}"
    resp3 = {}
    await post_async(url_prod, resp3, _json={}, headers=_auth_header(token))
    resp3[url_prod].raise_for_status()
    _log.info("Template version %d promoted to production", tv_id)


async def _config_template_setup(token: str) -> None:
    _log.info("Looking up device type '%s'…", BOOTSTRAP_SEMS_DEVICE_TYPE)
    dt = await _find_one("/web/api/devicetype/list", "name", BOOTSTRAP_SEMS_DEVICE_TYPE, token)
    if dt is None:
        _log.error("Device type '%s' not found in SEMS — skipping config/template setup", BOOTSTRAP_SEMS_DEVICE_TYPE)
        return
    device_type_id: int = dt["id"]
    _log.info("Device type '%s' found (id=%d)", BOOTSTRAP_SEMS_DEVICE_TYPE, device_type_id)

    content = _load_config_content()
    config_id = await _ensure_config(token, device_type_id, content)
    template = await _ensure_template(token, device_type_id)
    await _push_to_production(token, template, config_id)


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def bootstrap_sems() -> None:
    """
    Run the full SEMS bootstrap. Called once at API startup before the SmartEMS
    token loop so the service account is guaranteed to exist when SmartEMS.init()
    first attempts to authenticate.
    """
    _log.info("═══ SEMS Bootstrap — starting ═══")

    if SEMS_USER == _FIXTURE_USER:
        _log.error(
            "SEMS_USER is set to the fixture admin account ('%s')."
            " Bootstrap requires a dedicated service account — "
            "set SEMS_USER (and SEMS_PW) to non-fixture credentials and restart.",
            _FIXTURE_USER,
        )
        _log.warning("═══ SEMS Bootstrap — aborted (fixture credentials detected) ═══")
        return

    try:
        ready = await _health_check()
        if not ready:
            _log.warning("═══ SEMS Bootstrap — skipped (SEMS unreachable) ═══")
            return

        fixture_ran = await _fixture_setup()

        _log.info("Authenticating as service account '%s'…", SEMS_USER)
        token = await _login(SEMS_USER, SEMS_PW)
        if token is None:
            _log.warning(
                "Cannot authenticate as '%s' — service account not available, skipping remaining bootstrap steps",
                SEMS_USER,
            )
            _log.info("═══ SEMS Bootstrap — done (partial) ═══")
            return
        _log.info("Authenticated as '%s'", SEMS_USER)

        if fixture_ran:
            await _disable_fixture_admin(token)

        await _config_template_setup(token)

    except Exception as ex:
        _log.error("Bootstrap encountered an unexpected error — continuing startup: %s", ex, exc_info=True)

    _log.info("═══ SEMS Bootstrap — done ═══")
