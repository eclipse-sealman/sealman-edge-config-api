from helper import DeviceInfo
from smart_ems import SmartEMS
from ..schemas import NatConfig


async def get_smart_ems_config_nat(device: str):
    # TODO: Do we want to improve the error handling here?
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_info.check_nat_support()

    device_variables_dict = device_info.get_device_variables_dict()
    nat_rules = []

    if device_variables_dict.get("nat_enabled", "false") == "true":

        x = 1
        # Loop to process nat_machine_x elements
        while True:
            machine_key = f"nat_machine_{x}"
            lan2_key = f"{machine_key}_lan2"
            lan3_key = f"{machine_key}_lan3"

            if machine_key not in device_variables_dict:
                break

            nat_rules.append({
                "name": device_variables_dict.get(machine_key, ""),
                "extIp": device_variables_dict.get(lan2_key, ""),
                "intIp": device_variables_dict.get(lan3_key, "")
            })

            x += 1

    return NatConfig(
        nat_enabled=device_variables_dict.get("nat_enabled", "false"),
        nat_rules=nat_rules
    )
