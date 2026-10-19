import json
from helper import DeviceInfo
from smart_ems import SmartEMS
from ..schemas import PortForwardingConfig


async def get_smart_ems_config_port_forwarding(device: str):
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()

    rules = []

    port_forwarding_settings_raw = device_info.get_variable_value("port_forwarding_settings")
    try:
        port_forwarding_settings = json.loads(port_forwarding_settings_raw)
        for rule_data in port_forwarding_settings.get("rules", []):
            rules.append({
                "name": rule_data.get("name", ""),
                "interface": rule_data.get("interface", ""),
                "srcPort": int(rule_data.get("srcPort", 0)),
                "destAddr": rule_data.get("destAddr", ""),
                "destPort": int(rule_data.get("destPort", 0)),
            })
    except (TypeError, ValueError, AttributeError):
        rules = []

    return PortForwardingConfig(rules=rules)
