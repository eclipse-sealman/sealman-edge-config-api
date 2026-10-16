"""Extension/upstream health checks: `check_upstream_health(upstream) -> (status, detail)`,
called from `registry.py::check_extension_health` when a caller hits
`POST /extensions/{name}/health-check`. Not a background scheduler, never triggered by
a plain `GET` or by any write route. Persisted `last_status`/`last_detail`/`last_checked_at`
(extension_upstreams) simply reflect whichever check ran most recently; `GET /extensions`/
`GET /extensions/{name}` just return those unchanged.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion

from .upstreams import iotedge as iotedge_upstream

logger = logging.getLogger("EdgeConfigAPI")

_HEALTH_CHECK_TIMEOUT = 5.0


def _version_satisfies(actual_version: Optional[str], expected_version: str) -> bool:
    """True if `actual_version` matches `expected_version`, which may be either an exact
    string (back-compat with manifests that just pin a literal version) or a PEP 440
    specifier set (e.g. `>=0.1.0`, `~=1.2`, `==1.2.3,<2.0`). A bare version like `1.2.3`
    is not itself a valid specifier (no operator), so the exact-match check is tried
    first rather than only falling back to it on `InvalidSpecifier`."""
    if actual_version == expected_version:
        return True
    if actual_version is None:
        return False
    try:
        specifier = SpecifierSet(expected_version)
    except InvalidSpecifier:
        return False
    try:
        return specifier.contains(actual_version, prereleases=True)
    except InvalidVersion:
        return False


async def _check_http_upstream(upstream: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    base_url = upstream.get("base_url")
    if not base_url:
        return "unknown", "No base_url configured for this upstream"

    target = base_url.rstrip("/") + (upstream.get("health_path") or "/health")
    try:
        # A short-lived client per check, not a module-level singleton: health checks are
        # infrequent (request-triggered, not a hot dispatch path) and this sidesteps the
        # "client bound to whichever event loop first used it" issue a shared client has
        # across independent asyncio runs (e.g. one test per event loop).
        async with httpx.AsyncClient() as client:
            resp = await client.get(target, timeout=_HEALTH_CHECK_TIMEOUT)
    except httpx.HTTPError as exc:
        return "unhealthy", f"Health check request to {target} failed: {exc}"

    if resp.status_code >= 400:
        return "unhealthy", f"Health check returned HTTP {resp.status_code}"

    expected_version = upstream.get("expected_version")
    if expected_version:
        try:
            body = resp.json()
        except ValueError:
            return "unhealthy", "Health check response was not valid JSON; cannot verify expected_version"
        version_field = upstream.get("version_field") or "version"
        actual_version = body.get(version_field) if isinstance(body, dict) else None
        if not _version_satisfies(actual_version, expected_version):
            return "unhealthy", f"Expected version '{expected_version}', got '{actual_version}'"

    return "healthy", None


async def check_upstream_health(upstream: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Runs one upstream's health probe based on its persisted `type`. Never raises. Any
    probe failure (timeout, unreachable host, unreadable twin) is reported back as a
    `(status, detail)` pair rather than an exception, so callers can `asyncio.gather`
    several of these without a `return_exceptions=True` dance.

    Hard-capped at `_HEALTH_CHECK_TIMEOUT` regardless of upstream type: an `iotedge`
    check alone can otherwise make two sequential 15s-timeout IoT Hub calls (up to ~30s)
    inside `_resolve_canary_agent_modules`, which is fine for the one-off registration-time
    probe but not for something that can run on every `GET /extensions` request.
    """
    if upstream["type"] == "http":
        coro = _check_http_upstream(upstream)
    elif upstream["type"] == "iotedge":
        coro = iotedge_upstream.check_module_health(
            upstream.get("module_name"), upstream.get("health_device_query")
        )
    else:
        return "unknown", f"No health check implemented for upstream type '{upstream['type']}'"

    try:
        return await asyncio.wait_for(coro, timeout=_HEALTH_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        return "unknown", f"Health check timed out after {_HEALTH_CHECK_TIMEOUT}s"
