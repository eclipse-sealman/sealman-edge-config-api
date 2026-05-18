import re
from helper import DeviceInfo
from smart_ems import SmartEMS
from ..schemas import PortForwardingConfig


async def get_smart_ems_config_portforwarding(device: str):
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()

    device_variables_dict = device_info.get_device_variables_dict()
    rules = []

    index = 1
    while True:
        name_key = f"pfwd_name_{index}"
        value_key = f"pfwd_value_{index}"

        if name_key not in device_variables_dict or value_key not in device_variables_dict:
            break

        name = device_variables_dict.get(name_key)
        value = device_variables_dict.get(value_key)

        match = re.match(
            r"iifname (\w+) tcp dport (\d+) dnat to ([\d\.]+):(\d+)",
            value
        )

        if match:
            rules.append({
                "name": name,
                "interface": match.group(1),
                "srcPort": int(match.group(2)),
                "destAddr": match.group(3),
                "destPort": int(match.group(4))
            })

        index += 1

    return PortForwardingConfig(rules=rules)
