import asyncio
import logging
import re
from typing import Any, Callable, Dict, Optional

from starlette.datastructures import QueryParams

from constants import IOT_HUB_NAME
from async_requests import post_async
from db.repos.device import DeviceRepository
from exceptions import IoTBackendAPIError, APIError
from helper import get_iothub_auth_headers


logger = logging.getLogger("EdgeConfigAPI")
_META_DEEP_OBJECT_RE = re.compile(r"^meta\[(.*)]$")


def _merge_metadata(platform_meta: Dict[str, Any], device_meta: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key in platform_meta.keys():
        if key in device_meta:
            merged[key] = {"value": device_meta[key], "source": "platform"}
        else:
            merged[key] = {"value": None, "source": "platform"}
    for key, value in device_meta.items():
        if key not in platform_meta:
            merged[key] = {"value": value, "source": "device"}
    return merged


def _extract_metadata_filters(query_params: QueryParams | None) -> Dict[str, Optional[str]]:
    if query_params is None:
        return {}

    metadata_filters: Dict[str, Optional[str]] = {}

    for raw_key, raw_value in query_params.multi_items():
        match = _META_DEEP_OBJECT_RE.match(raw_key)
        if not match:
            continue

        meta_key = match.group(1)
        if not meta_key:
            raise APIError("Metadata filter key cannot be empty", 400)

        if meta_key in metadata_filters:
            raise APIError(f"Duplicate metadata filter for key '{meta_key}'", 400)

        # `?meta[key]` (and `?meta[key]=`) means key-only non-empty match.
        metadata_filters[meta_key] = raw_value if raw_value != "" else None

    return metadata_filters


async def get_devices(
    repo: DeviceRepository,
    filter_device: Callable[[Dict], bool],
    query_params: QueryParams | None = None,
):
    metadata_filters = _extract_metadata_filters(query_params)
    matching_device_ids = None

    if metadata_filters:
        matching_device_ids = set(
            await repo.get_device_ids_by_metadata_filters(metadata_filters)
        )

        if not matching_device_ids:
            return []

    devices = await repo.get_devices_joined_snapshot()
    platform_meta = await repo.get_platform_meta_keys()

    devices_output = []

    for device in devices:
        device_id = device.get("device_id")

        if matching_device_ids is not None and device_id not in matching_device_ids:
            continue

        # ABAC scope filter against raw device_meta
        device_meta = device.get("device_meta") or {}
        if not filter_device(device_meta):
            continue

        dev_output = {}
        dev_output["deviceId"] = device_id
        dev_output.setdefault("lastSeenInRange", False)
        device_status = device.get("connection_state", "Unknown") or "Unknown"
        dev_output.setdefault("deviceStatus", device_status)
        dev_output.setdefault("iotEdgeRuntime", device_status)
        dev_output.setdefault("iotHub", "Unknown")
        dev_output.setdefault("sems", "Unknown")
        dev_output.setdefault("vpn", "Unknown")

        dev_output["deviceMetadata"] = _merge_metadata(platform_meta, device_meta)

        dev_output["createdAt"] = device.get("created_at", None)
        dev_output["updatedAt"] = device.get("updated_at", None)

        devices_output.append(dev_output)

    return devices_output


async def populate_cache_from_iot_hub_query(repo: DeviceRepository):
    logger.info("Populating device cache from IoT Hub query")
    get_devices_url = f"https://{IOT_HUB_NAME}/devices/query?api-version=2021-04-12"
    devices = []
    continuation_token = None
    has_more_pages = True

    while has_more_pages:
        responses = {}
        headers = get_iothub_auth_headers()

        if continuation_token:
            headers["x-ms-continuation"] = continuation_token

        await asyncio.gather(
            post_async(
                get_devices_url,
                responses,
                headers={
                    **headers,
                    "Content-Type": "application/json",
                    "x-ms-max-item-count": "2000",
                },
                _json={
                    "query": "SELECT deviceId, connectionState FROM devices"
                },
                timeout=15,
            ),
        )
        resp = responses[get_devices_url]

        if resp.status_code == 200:
            for device_obj in resp.json():
                device_id = device_obj["deviceId"]
                connection_state = device_obj["connectionState"]

                device = {
                    "deviceName": device_id,
                    "deviceStatus": connection_state
                }

                devices.append(device)

            continuation_token = resp.headers.get("x-ms-continuation")
            has_more_pages = continuation_token is not None
        else:
            raise IoTBackendAPIError(
                f"could not retrieve any device from iot-hub: {responses[get_devices_url].text}",
                responses[get_devices_url].status_code,
            )

    # Override deviceStatus with $edgeHub module connectionState, which reflects
    # the actual live connection for IoT Edge devices (device-level connectionState
    # is always Disconnected for Edge devices).
    try:
        edgehub_map = await get_device_map_edgehub()
        for device in devices:
            edgehub_state = edgehub_map.get(device["deviceName"])
            if edgehub_state is not None:
                device["deviceStatus"] = edgehub_state
    except Exception as e:
        logger.warning(
            "Could not fetch $edgeHub connection states, falling back to device-level connectionState: %s",
            e,
        )

    await repo.upsert_device_snapshot(devices)
    logger.info("Device cache populated successfully with %d devices", len(devices))


async def get_device_map_edgehub():
    get_devices_url = f"https://{IOT_HUB_NAME}/devices/query?api-version=2021-04-12"
    device_map = {}
    continuation_token = None
    has_more_pages = True

    while has_more_pages:
        responses = {}
        headers = get_iothub_auth_headers()

        if continuation_token:
            headers["x-ms-continuation"] = continuation_token

        await asyncio.gather(
            post_async(
                get_devices_url,
                responses,
                headers={
                    **headers,
                    "Content-Type": "application/json",
                },
                _json={
                    "query": "SELECT deviceId, moduleId, connectionState FROM devices.modules WHERE moduleId = '$edgeHub'"
                },
                timeout=15,
            ),
        )
        resp = responses[get_devices_url]

        if resp.status_code == 200:
            for device_obj in resp.json():
                device_id = device_obj["deviceId"]
                connection_state = device_obj["connectionState"]
                device_map[device_id] = connection_state

            continuation_token = resp.headers.get("x-ms-continuation")
            has_more_pages = continuation_token is not None
        else:
            raise IoTBackendAPIError(
                f"could not retrieve any device from iot-hub: {responses[get_devices_url].text}",
                responses[get_devices_url].status_code,
            )

    return device_map