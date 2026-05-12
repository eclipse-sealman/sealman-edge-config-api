import re
from helper import DeviceInfo, TemplateVariables, is_ip_in_subnet
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import SEMSError
from ..schemas import NatConfig


async def post_smart_ems_config_nat(device: str, config: NatConfig):
    desired_nat_rules = config.get_nat_rules()
    if len(desired_nat_rules) > 10:
        raise SEMSError("maximum of 10 NAT rules allowed", status_code=400)

    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_info.check_nat_support()
    device_id = device_info.get()["id"]

    device_variables_dict = device_info.get_device_variables_dict()

    # Collect and remove all keys starting with "nat_machine_" and an integer and optionally "_lan" and an integer
    keys_to_remove = [
        key for key in device_variables_dict if re.match(r"^nat_machine_\d+(_lan\d+)?$", key)]
    for key in keys_to_remove:
        del device_variables_dict[key]

    if not config.nat_enabled:
        # Setting nat_enabled to false doesn't work because of the template. We have to remove it.
        device_variables_dict.pop("nat_enabled", None)
    else:
        device_variables_dict.update({"nat_enabled": "true"})

        ip_lan_3 = device_variables_dict.get("ip_lan_3", "")
        subnet_lan_3 = device_variables_dict.get("subnet_lan_3", "")

        seen_ext_ips = set()
        seen_int_ips = set()
        # Iterate over the desired_nat_rules, validate the input, and add them to the device_variables_dict
        for i in range(len(desired_nat_rules)):
            ext_ip = desired_nat_rules[i].extIp
            int_ip = desired_nat_rules[i].intIp

            if ip_lan_3 and subnet_lan_3:
                if not is_ip_in_subnet(int_ip, ip_lan_3, subnet_lan_3):
                    raise SEMSError(
                        f"NAT ip address {int_ip} out of range for specified LAN address {ip_lan_3} and subnet {subnet_lan_3}", status_code=400)

            if ext_ip == int_ip:
                raise SEMSError(
                    "ExtIp and IntIp cannot be the same", status_code=400)

            if ext_ip in seen_ext_ips or int_ip in seen_int_ips:
                raise SEMSError(
                    "No repeat extIp or intIp between nat rules allowed", status_code=400)
            seen_ext_ips.add(ext_ip)
            seen_int_ips.add(int_ip)

            # Construct and assign nat rule
            machine_key = f"nat_machine_{i + 1}"
            lan2_key = f"{machine_key}_lan2"
            lan3_key = f"{machine_key}_lan3"

            nat_rule = {
                machine_key: desired_nat_rules[i].name,
                lan2_key: ext_ip,
                lan3_key: int_ip
            }
            
            device_variables_dict.update(nat_rule)

    device_variables = TemplateVariables()
    device_variables.add_from_dict(device_variables_dict)

    body = generate_resp_from_device_info(device_info.get())
    body["variables"] = device_variables.get()
    body["reinstallConfig1"] = True

    if await SmartEMS.edit_device(device_id, body):
        return config
    else:
        raise SEMSError(f"could not update device {device}", status_code=400)
