import json
from helper import DeviceInfo
from smart_ems import SmartEMS


async def get_smart_ems_config_cellular(device: str):
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()

    interface = {}

    # check if device is capable of cellular
    cellular = device_info.get_variable_value("cellular", False) == "true"

    if cellular:
        cellular_settings_raw = device_info.get_variable_value("cellular_settings")
        try:
            cellular_settings = json.loads(cellular_settings_raw)
            interface = {
                "apn": cellular_settings.get("apn", ""),
                "pin": cellular_settings.get("pin", ""),
                "access_number": cellular_settings.get("access_number", ""),
                "auth_method": cellular_settings.get("auth_method", ""),
                "username": cellular_settings.get("username", ""),
                "password": cellular_settings.get("password", ""),
                "state": cellular_settings.get("state", "off"),
            }
        except (TypeError, ValueError, AttributeError):
            cellular = False

    return {
        "cellular": cellular,
        "interface": interface,
    }

