"""IoT Edge module dispatch for `iotedge`-type upstreams: direct-method invocation,
module-twin read, and module-twin write.
"""

import json
import logging
import re
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from async_requests import get_async, patch_async, post_async
from blob_service import BlobContainerContext
from constants import BLOB_SAS_TOKEN_MODULE_CONF, IOT_HUB_NAME, PUBLIC_STORAGE_ACCOUNT_NAME
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers

from .. import body_validation

logger = logging.getLogger("EdgeConfigAPI")

_API_VERSION = "2021-04-12"

# IoT Hub rejects direct-method payloads above 128 KB.
_DIRECT_METHOD_MAX_PAYLOAD_BYTES = 128 * 1024

# IoT Hub identity registry rules for deviceId/moduleId.
_IOTHUB_ID_RE = re.compile(r"^[A-Za-z0-9\-.%_*?!(),:=@$']{1,128}$")


def _encode_id(value: str) -> str:
    return quote(value, safe="")


def _module_url(device_id: str, module_name: str, suffix: str = "") -> str:
    return (
        f"https://{IOT_HUB_NAME}/twins/{_encode_id(device_id)}/modules/{_encode_id(module_name)}"
        f"{suffix}?api-version={_API_VERSION}"
    )


def _twin_url(device_id: str, module_name: str) -> str:
    return _module_url(device_id, module_name)


def _resolve_device_id(request: Request, route: Dict[str, Any]) -> str:
    """Resolves the target IoT Hub device: the route's own scoping config
    (`scoped`/`scope_param`/`scope_in`) if set, else an explicit `device_id` query param."""
    if route.get("scoped"):
        scope_in = route.get("scope_in") or "query"
        scope_param = route.get("scope_param") or "device_id"
        device_id = (
            request.path_params.get(scope_param)
            if scope_in == "path"
            else request.query_params.get(scope_param)
        )
    else:
        device_id = request.query_params.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="Missing target device id for this iotedge route")
    if not _IOTHUB_ID_RE.fullmatch(device_id):
        raise HTTPException(status_code=400, detail="Invalid target device id for this iotedge route")
    return device_id


async def _read_json_body(request: Request, body: Optional[bytes], limit: int) -> Optional[dict]:
    raw = body if body is not None else await body_validation.read_limited_body(request, limit)
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail=f"Request body exceeds {limit} bytes")
    return body_validation.parse_json(raw)


async def _twin_read(device_id: str, module_name: str) -> dict:
    responses: Dict[str, Any] = {}
    url = _twin_url(device_id, module_name)
    await get_async(url, responses, headers=get_iothub_auth_headers(), timeout=15)
    resp = responses[url]
    if resp.status_code >= 400:
        raise IoTBackendAPIError(resp.text, resp.status_code)
    return resp.json()


async def _twin_write(device_id: str, module_name: str, desired: Optional[dict]) -> dict:
    """Uploads `desired` to blob storage and patches the module twin with only
    `configBlobUrl`/`configId` (this repo's existing blob-indirection convention,
    see `post_module_twin_config.py`)."""
    if not PUBLIC_STORAGE_ACCOUNT_NAME:
        raise IoTBackendAPIError("Public storage account name is not set. Cannot upload file.", 500)
    if not BLOB_SAS_TOKEN_MODULE_CONF or not BLOB_SAS_TOKEN_MODULE_CONF.strip():
        raise IoTBackendAPIError("BLOB_SAS_TOKEN_MODULE_CONF is not configured. SAS token is required for Edge Device blob access.", 500)

    config_id = uuid.uuid4().hex
    try:
        async with BlobContainerContext(
            PUBLIC_STORAGE_ACCOUNT_NAME, "iotedge-device-twin", sas_token=BLOB_SAS_TOKEN_MODULE_CONF
        ) as container:
            blob = container.get_blob_client(f"{device_id}/{module_name}/{config_id}")
            await blob.upload_blob(json.dumps(desired or {}), overwrite=True)
    except Exception as exc:
        raise IoTBackendAPIError(f"could not upload data object to blob storage: {exc}", 400)

    config_blob_url = (
        f"https://{PUBLIC_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/iotedge-device-twin/"
        f"{_encode_id(device_id)}/{_encode_id(module_name)}/{config_id}?{BLOB_SAS_TOKEN_MODULE_CONF}"
    )
    patch = {"properties": {"desired": {"configBlobUrl": config_blob_url, "configId": config_id}}}

    responses: Dict[str, Any] = {}
    url = _twin_url(device_id, module_name)
    await patch_async(url, responses, _json=patch, headers=get_iothub_auth_headers())
    resp = responses[url]
    if resp.status_code >= 400:
        raise IoTBackendAPIError(resp.text, resp.status_code)
    # configBlobUrl embeds the SAS token and must not reach the caller.
    return {"configId": config_id}


async def _direct_method(device_id: str, module_name: str, method_name: str, payload: Optional[dict]) -> JSONResponse:
    """Invokes a direct method."""
    url = _module_url(device_id, module_name, "/methods")
    body = {
        "methodName": method_name,
        "connectTimeoutInSeconds": 30,
        "responseTimeoutInSeconds": 30,
        "payload": payload if payload is not None else {},
    }
    responses: Dict[str, Any] = {}
    await post_async(url, responses, _json=body, headers=get_iothub_auth_headers(), timeout=45)
    resp = responses[url]
    if resp.status_code >= 400:
        raise IoTBackendAPIError(resp.text, resp.status_code)

    result = resp.json()
    # TODO: clamp the device-controlled status to 200-599 and map 401/403 to 502 so a device cannot log the user out.
    device_status = result.get("status", 200)
    return JSONResponse(result.get("payload"), status_code=device_status)


async def dispatch(
    request: Request, upstream: Dict[str, Any], route: Dict[str, Any], body: Optional[bytes] = None
) -> JSONResponse:
    device_id = _resolve_device_id(request, route)
    module_name = upstream["module_name"]
    operation = route.get("iotedge_operation") or "direct_method"

    if operation == "twin_read":
        return JSONResponse(await _twin_read(device_id, module_name))
    if operation == "twin_write":
        payload = await _read_json_body(request, body, body_validation.MAX_BUFFERED_BODY_BYTES)
        return JSONResponse(await _twin_write(device_id, module_name, payload))

    payload = await _read_json_body(request, body, _DIRECT_METHOD_MAX_PAYLOAD_BYTES)
    return await _direct_method(device_id, module_name, route["method_name"], payload)


async def _resolve_canary_agent_modules(health_device_query: str) -> Optional[Tuple[str, dict]]:
    """Resolves one canary device via the given IoT Hub device-query/target-condition
    string, then reads its `$edgeAgent` twin's reported modules map. Returns `(device_id,
    modules)` or `None` if the query fails, resolves to zero devices."""
    query_responses: Dict[str, Any] = {}
    query_url = f"https://{IOT_HUB_NAME}/devices/query?api-version={_API_VERSION}"
    await post_async(
        query_url,
        query_responses,
        _json={"query": f"SELECT deviceId FROM devices WHERE {health_device_query}"},
        headers={**get_iothub_auth_headers(), "Content-Type": "application/json"},
        timeout=15,
    )
    resp = query_responses[query_url]
    if resp.status_code != 200:
        return None
    devices = resp.json()
    if not devices:
        return None

    device_id = devices[0]["deviceId"]
    agent_responses: Dict[str, Any] = {}
    agent_url = f"https://{IOT_HUB_NAME}/twins/{_encode_id(device_id)}/modules/$edgeAgent?api-version={_API_VERSION}"
    await get_async(agent_url, agent_responses, headers=get_iothub_auth_headers(), timeout=15)
    agent_resp = agent_responses[agent_url]
    if agent_resp.status_code != 200:
        return None

    modules = (agent_resp.json().get("properties", {}) or {}).get("reported", {}).get("modules", {}) or {}
    return device_id, modules


async def probe_registration_health(module_name: str, health_device_query: str) -> Optional[str]:
    """Best-effort registration-time typo guard for `health_device_query`: resolves one
    canary device via the same IoT Hub device-query syntax used for deployment targeting,
    then reads its `$edgeAgent` twin and checks `module_name` appears in the reported
    `modules` map. Returns a human-readable warning string (never raises, never rejects
    registration)."""
    try:
        resolved = await _resolve_canary_agent_modules(health_device_query)
        if resolved is None:
            return None  # can't evaluate right now 
        device_id, modules = resolved

        module_entry = modules.get(module_name)
        if module_entry is None or "runtimeStatus" not in module_entry:
            return (
                f"Canary device '{device_id}' (matched by health_device_query) has no module "
                f"'{module_name}' in its $edgeAgent reported modules."
            )
        return None
    except Exception as exc:  # best-effort only, never blocks registration
        logger.warning(f"iotedge registration-time health probe failed, skipping: {exc}")
        return None


_HEALTHY_RUNTIME_STATUSES = {"running"}
_UNHEALTHY_RUNTIME_STATUSES = {"stopped", "failed", "backoff", "unhealthy"}


async def check_module_health(module_name: Optional[str], health_device_query: Optional[str]) -> Tuple[str, Optional[str]]:
    """Health status for one `iotedge` upstream: resolves `health_device_query` to a
    canary device and reads its `$edgeAgent`-reported `runtimeStatus` for `module_name`
    (see `_resolve_canary_agent_modules`). `unknown` (never `healthy`/`unhealthy`) whenever
    the query isn't configured, resolves to zero devices, or the canary's twin can't be
    read."""
    if not health_device_query:
        return "unknown", "No health_device_query configured for this upstream"
    try:
        resolved = await _resolve_canary_agent_modules(health_device_query)
        if resolved is None:
            return "unknown", "health_device_query resolved to zero devices, or its canary's twin could not be read"
        device_id, modules = resolved

        module_entry = modules.get(module_name)
        if module_entry is None or "runtimeStatus" not in module_entry:
            return "unknown", f"Canary device '{device_id}' has no reported runtimeStatus for module '{module_name}'"

        runtime_status = module_entry["runtimeStatus"]
        if runtime_status in _HEALTHY_RUNTIME_STATUSES:
            return "healthy", None
        if runtime_status in _UNHEALTHY_RUNTIME_STATUSES:
            return "unhealthy", f"Canary device '{device_id}' reports module '{module_name}' runtimeStatus='{runtime_status}'"
        return "unknown", f"Canary device '{device_id}' reports unrecognized runtimeStatus='{runtime_status}' for module '{module_name}'"
    except Exception as exc:  # best-effort only
        logger.warning(f"iotedge health check failed for module '{module_name}': {exc}")
        return "unknown", f"Health check errored: {exc}"
