import json

from helper import DeviceInfo, TemplateVariableType
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import SEMSError
from ..schemas import CellularInterface


async def post_smart_ems_config_cellular(device: str, interface: CellularInterface):
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_id = device_info.get()["id"]

    interface = interface.model_dump()

    cellular_settings = {
        "apn": interface.get("apn", ""),
        "pin": interface.get("pin", ""),
        "access_number": interface.get("access_number", ""),
        "auth_method": interface.get("auth_method", ""),
        "username": interface.get("username", ""),
        "password": interface.get("password", ""),
        "state": interface.get("state", "off")
    }

    device_info.upsert_variable(
        "cellular_settings",
        json.dumps(cellular_settings, separators=(",", ":")),
        TemplateVariableType.JSON_OBJECT,
    )

    body = generate_resp_from_device_info(device_info.get())
    body["reinstallConfig1"] = True
    if await SmartEMS.edit_device(device_id, body):
        return interface
    else:
        raise SEMSError(f"could not update device {device}", status_code=400)
