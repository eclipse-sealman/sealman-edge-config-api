"""Health and version verification for extension upstreams.

Evaluates upstream status across three dimensions:
1. Presence: is the micro-service / edge module deployed?
2. Liveness: is the upstream reachable and healthy?
3. Compatibility: does the running version satisfy expected_version?
"""
import logging
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from db.repos.extension import ExtensionRepository
from exceptions import IoTBackendAPIError
from extensions import iotedge_transport

logger = logging.getLogger("EdgeConfigAPI")


def version_satisfies(detected: Any, spec: Optional[str]) -> bool:
    """Return True if detected version satisfies the PEP 440 / SemVer specifier."""
    if not spec:
        return True
    if not detected:
        return False
    try:
        return Version(str(detected)) in SpecifierSet(str(spec))
    except (InvalidVersion, InvalidSpecifier):
        return str(detected) == str(spec)


def _dig(obj: Any, dotted: str) -> Any:
    """Read a dotted field path out of a nested dictionary."""
    cur = obj
    for part in str(dotted).split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


async def _http_health(up: dict[str, Any]) -> dict[str, Any]:
    base_url = (up.get("base_url") or "").rstrip("/")
    health_path = up.get("health_path") or "/health"
    version_field = up.get("version_field") or "version"
    expected = up.get("expected_version")

    result: dict[str, Any] = {
        "ok": False,
        "transport": "http",
        "base_url": base_url,
        "state": "unknown",
        "detected_version": None,
        "expected_version": expected,
        "detail": None,
    }

    if not base_url:
        result.update(state="not_deployed", detail="Upstream has no base_url.")
        return result

    url = f"{base_url}{health_path}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
    except httpx.RequestError as exc:
        result.update(state="not_deployed", detail=f"Micro-service unreachable: {exc}")
        return result

    if resp.status_code >= 400:
        result.update(state="unhealthy", detail=f"Health endpoint returned {resp.status_code}.")
        return result

    try:
        body = resp.json()
    except ValueError:
        body = {}

    detected = _dig(body, version_field) if isinstance(body, dict) else None
    result["detected_version"] = detected

    if expected and not version_satisfies(detected, expected):
        result.update(
            state="version_mismatch",
            detail=f"Running version {detected!r} does not satisfy '{expected}'.",
        )
        return result

    result.update(ok=True, state="ok")
    return result


async def _iotedge_health(up: dict[str, Any], device_id: str) -> dict[str, Any]:
    module_name = up.get("module_name") or ""
    expected_version = up.get("expected_version")
    version_source = up.get("version_source") or "image_tag"
    twin_version_property = up.get("twin_version_property") or "version"
    health_method = up.get("health_method")

    result: dict[str, Any] = {
        "ok": False,
        "transport": "iotedge",
        "device_id": device_id,
        "module_id": module_name,
        "state": "unknown",
        "runtime_status": None,
        "connection_state": None,
        "image": None,
        "detected_version": None,
        "expected_version": expected_version,
        "detail": None,
    }

    # 1. Read $edgeAgent reported properties
    try:
        agent = await iotedge_transport.get_module_twin(device_id, "$edgeAgent")
    except IoTBackendAPIError as exc:
        if exc.status_code == 404:
            result.update(state="not_deployed", detail="Device or $edgeAgent not found in IoT Hub.")
            return result
        result.update(state="unhealthy", detail=f"IoT Hub error: {exc.message}")
        return result
    except Exception as exc:
        result.update(state="unhealthy", detail=f"Failed to read $edgeAgent twin: {exc}")
        return result

    reported = (agent.get("properties", {}) or {}).get("reported", {}) or {}
    modules = {**(reported.get("modules") or {}), **(reported.get("systemModules") or {})}
    mod = modules.get(module_name)
    if not mod:
        result.update(
            state="not_deployed",
            detail=f"Module '{module_name}' is not part of the device's deployment.",
        )
        return result

    runtime = mod.get("runtimeStatus")
    image = (mod.get("settings") or {}).get("image")
    image_tag = image.rsplit(":", 1)[-1] if image and ":" in image else None
    result["runtime_status"] = runtime
    result["image"] = image

    # 2. Module twin for reported version / connectionState
    twin_reported = {}
    try:
        twin = await iotedge_transport.get_module_twin(device_id, module_name)
        result["connection_state"] = twin.get("connectionState")
        twin_reported = (twin.get("properties", {}) or {}).get("reported", {}) or {}
    except IoTBackendAPIError:
        pass  # Module twin not initialized yet
    except Exception as exc:
        logger.warning(f"Could not read twin for {device_id}/{module_name}: {exc}")

    # 3. Detect version
    if version_source == "twin_reported":
        detected = _dig(twin_reported, twin_version_property)
    else:
        detected = image_tag
    result["detected_version"] = detected

    # 4. Check runtime status
    if runtime and runtime != "running":
        result.update(state="unhealthy", detail=f"runtimeStatus={runtime}")
        return result

    # 5. Optional liveness direct method probe
    if health_method:
        try:
            await iotedge_transport.invoke_direct_method(device_id, module_name, health_method, None)
        except IoTBackendAPIError as exc:
            result.update(state="offline", detail=f"Liveness probe failed: {exc.message}")
            return result
        except Exception as exc:
            result.update(state="offline", detail=f"Liveness probe error: {exc}")
            return result

    # 6. Check version constraint
    if expected_version and not version_satisfies(detected, expected_version):
        result.update(
            state="version_mismatch",
            detail=f"Deployed version {detected!r} does not satisfy '{expected_version}'.",
        )
        return result

    result.update(ok=True, state="ok")
    return result


async def check_upstream(up: dict[str, Any], device_id: Optional[str] = None) -> dict[str, Any]:
    """Health check a single upstream definition."""
    if up.get("type") == "iotedge":
        if not device_id:
            return {
                "ok": None,
                "transport": "iotedge",
                "module_id": up.get("module_name"),
                "state": "unknown",
                "detail": "Provide device_id query param to health-check an iotedge upstream.",
            }
        return await _iotedge_health(up, device_id)
    return await _http_health(up)


async def extension_health(
    extension_repo: ExtensionRepository, name: str, device_id: Optional[str] = None
) -> dict[str, Any]:
    """Aggregate health status for all upstreams belonging to an extension."""
    ext = await extension_repo.get_extension(name)
    if not ext:
        raise HTTPException(status_code=404, detail=f"Extension '{name}' not found")

    upstreams = ext.get("upstreams") or {}
    checks = {key: await check_upstream(up, device_id) for key, up in upstreams.items()}
    verdicts = [c.get("ok") for c in checks.values() if c.get("ok") is not None]
    overall = bool(verdicts) and all(verdicts)

    return {
        "extension": name,
        "device_id": device_id,
        "ok": overall,
        "upstreams": checks,
    }
