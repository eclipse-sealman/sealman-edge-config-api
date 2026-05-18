import asyncio
import logging

from async_requests import delete_async, get_async
from smart_ems import SmartEMS
from db.repos.device import DeviceRepository
from constants import IOT_HUB_NAME
from helper import get_iothub_auth_headers
from exceptions import APIError, IoTBackendAPIError

logger = logging.getLogger("EdgeConfigAPI")

IOT_HUB_API_VERSION = "2021-04-12"


async def delete_device(
    device_id: str,
    repo: DeviceRepository,
):
    if not await repo.device_exists(device_id):
        raise APIError(f"Device {device_id} not found", 404)

    # 1. DELETE FROM SEMS
    sems_device = await SmartEMS.get_device_by_serial(device_id, require_template=False)
    if sems_device:
        await SmartEMS.delete_device(sems_device["id"])

    # 2. DELETE FROM IoTHub
    url = f"https://{IOT_HUB_NAME}/devices/{device_id}?api-version={IOT_HUB_API_VERSION}"
    headers = get_iothub_auth_headers()

    responses = {}
    await asyncio.gather(
        get_async(url, responses, headers=headers)
    )
    get_resp = responses[url]

    # If device does not exist in IoTHub anymore, treat it as idempotent success.
    if get_resp.status_code != 404:
        if get_resp.status_code != 200:
            raise IoTBackendAPIError(
                f"Could not retrieve device {device_id} from iot-hub before delete: {get_resp.text}",
                get_resp.status_code,
            )

        etag = get_resp.json().get("etag") or get_resp.headers.get("etag")
        if not etag:
            raise IoTBackendAPIError(
                f"Could not retrieve etag for device {device_id} from iot-hub.",
                502,
            )

        delete_headers = {**headers, "If-Match": etag}
        responses = {}
        await asyncio.gather(
            delete_async(url, responses, headers=delete_headers)
        )
        del_resp = responses[url]

        if del_resp.status_code not in [200, 202, 204, 404]:
            raise IoTBackendAPIError(
                f"Could not delete device {device_id} from iot-hub: {del_resp.text}",
                del_resp.status_code,
            )

    # 3. DELETE FROM DB (source of truth only after external systems succeeded)
    await repo.delete_device(device_id)
