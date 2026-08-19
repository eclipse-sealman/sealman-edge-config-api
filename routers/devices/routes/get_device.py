from typing import Any, Dict

from db.repos.device import DeviceRepository
from exceptions import APIError
from routers.general.routes.get_device_connection_status import (
    get_iot_hub_connection_status,
    get_sems_connection_status,
)
from routers.smart_ems.routes.get_smart_ems_device_info import get_smart_ems_device_summary
from smart_ems import SmartEMS


async def get_device(device_id: str, repo: DeviceRepository) -> Dict[str, Any]:
    device = await repo.get_device_joined_snapshot(device_id)
    if device is None:
        raise APIError(f"Device '{device_id}' not found", 404)

    smart_ems_device = await SmartEMS.get_device_by_serial(device_id)
    sems_summary = get_smart_ems_device_summary(smart_ems_device)
    sems_connection_status = get_sems_connection_status(smart_ems_device)
    iot_hub_status = await get_iot_hub_connection_status(device_id)

    device_metadata = device.get("device_metadata") or {}
    device_status = device.get("device_status", "Unknown") or "Unknown"

    return {
        "deviceId": device_id,
        "connectionStatus": {
            "deviceStatus": device_status,
            "iotEdgeRuntime": iot_hub_status["iotEdgeRuntime"],
            "iotHub": iot_hub_status["iotHub"],
            "sems": sems_connection_status,
            "vpn": "Unknown",
        },
        "deviceMetadata": device_metadata,
        "createdAt": device.get("created_at"),
        "updatedAt": device.get("updated_at"),
        "lastSeenAt": smart_ems_device.get("seenAt"),
        **sems_summary,
    }