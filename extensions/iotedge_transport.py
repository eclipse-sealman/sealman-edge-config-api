"""IoT Hub dispatch for 'iotedge' upstream routes (direct method / module twin
read / module twin write), reusing the exact IoT Hub REST calling conventions
already used by ``routers/cmd_proxy`` and ``routers/module_config`` (SAS/
workload-identity auth via ``helper.get_iothub_auth_headers()``, requests via
``async_requests``) instead of re-implementing IoT Hub auth from scratch.
"""
import asyncio
import json
from typing import Any, Optional

from async_requests import get_async, patch_async, post_async
from constants import IOT_HUB_NAME
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers

_API_VERSION = "2020-05-31-preview"


async def invoke_direct_method(
    device_id: str, module_name: str, method_name: str, payload: Optional[dict]
) -> Any:
    """Invoke a module direct method via the IoT Hub REST API.

    Mirrors ``routers/cmd_proxy/routes/post_device_cmd_*`` /
    ``routers/general/routes/post_module_method.py``.
    """
    url = f"https://{IOT_HUB_NAME}/twins/{device_id}/modules/{module_name}/methods?api-version={_API_VERSION}"
    data = {
        "connectTimeoutInSeconds": 10,
        "methodName": method_name,
        "responseTimeoutInSeconds": 25,
    }
    if payload is not None:
        data["payload"] = payload

    responses = {}
    headers = get_iothub_auth_headers()
    await asyncio.gather(post_async(url, responses, _json=data, headers=headers, timeout=45))

    resp = responses[url]
    if resp.status_code != 200:
        raise IoTBackendAPIError(resp.text, resp.status_code)
    try:
        return resp.json()
    except ValueError:
        return None


async def get_module_twin(device_id: str, module_name: str) -> dict:
    """Read the full module twin (reported + desired properties + metadata)."""
    url = f"https://{IOT_HUB_NAME}/twins/{device_id}/modules/{module_name}?api-version={_API_VERSION}"
    responses = {}
    headers = get_iothub_auth_headers()
    await asyncio.gather(get_async(url, responses, headers=headers, timeout=15))

    resp = responses[url]
    if resp.status_code == 404:
        raise IoTBackendAPIError(f"Module twin '{device_id}/{module_name}' not found", 404)
    if resp.status_code != 200:
        raise IoTBackendAPIError(resp.text, resp.status_code)
    return json.loads(resp.text)


async def patch_module_twin(device_id: str, module_name: str, patch: dict) -> dict:
    """Merge-patch the module twin's desired properties."""
    url = f"https://{IOT_HUB_NAME}/twins/{device_id}/modules/{module_name}?api-version={_API_VERSION}"
    body = {"properties": {"desired": patch or {}}}
    responses = {}
    headers = get_iothub_auth_headers()
    await asyncio.gather(patch_async(url, responses, _json=body, headers=headers, timeout=15))

    resp = responses[url]
    if resp.status_code != 200:
        raise IoTBackendAPIError(resp.text, resp.status_code)
    return json.loads(resp.text)
