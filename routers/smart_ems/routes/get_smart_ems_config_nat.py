import json
from helper import DeviceInfo
from smart_ems import SmartEMS
from ..schemas import NatConfig


async def get_smart_ems_config_nat(device: str):
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_info.check_nat_support()

    nat_rules = []

    nat_settings_raw = device_info.get_variable_value("nat_settings")
    try:
        nat_settings = json.loads(nat_settings_raw)
        nat_enabled = bool(nat_settings.get("enabled", False))

        for mapping in nat_settings.get("mappings", []):
            nat_rules.append(
                {
                    "name": mapping.get("name", ""),
                    "extIp": mapping.get("externalIp", ""),
                    "intIp": mapping.get("internalIp", ""),
                }
            )
    except (TypeError, ValueError):
        # If value is malformed, fall back to legacy format.
        nat_enabled = False

    return NatConfig(
        nat_enabled=nat_enabled,
        nat_rules=nat_rules
    )
