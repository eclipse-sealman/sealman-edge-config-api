import re
from helper import DeviceInfo, TemplateVariables
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import SEMSError
from routers.smart_ems.schemas import PortForwardingConfig



async def post_smart_ems_config_portforwarding(device: str, config: PortForwardingConfig):
    rules = config.get_rules()

    if len(rules) > 10:
        raise SEMSError("maximum of 10 port forwarding rules allowed", status_code=400)

    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_id = device_info.get()["id"]

    device_variables_dict = device_info.get_device_variables_dict()

    keys_to_remove = [
        key for key in device_variables_dict
        if re.match(r"^pfwd_(name|value)_\d+$", key)
    ]
    for key in keys_to_remove:
        del device_variables_dict[key]

    seen_names = set()
    seen_interface_ports = set()

    for rule in rules:
        if rule.name in seen_names:
            raise SEMSError("duplicate port forwarding rule names are not allowed", status_code=400)
        seen_names.add(rule.name)

        key = f"{rule.interface}:{rule.srcPort}"
        if key in seen_interface_ports:
            raise SEMSError(
                f"duplicate port forwarding for interface {rule.interface} and port {rule.srcPort}",
                status_code=400
            )
        seen_interface_ports.add(key)

    for i, rule in enumerate(rules, start=1):
        name_key = f"pfwd_name_{i}"
        value_key = f"pfwd_value_{i}"

        value = (
            f"iifname {rule.interface} tcp dport {rule.srcPort} "
            f"dnat to {rule.destAddr}:{rule.destPort}"
        )

        device_variables_dict.update({
            name_key: rule.name,
            value_key: value
        })

    device_variables = TemplateVariables()
    device_variables.add_from_dict(device_variables_dict)

    body = generate_resp_from_device_info(device_info.get())
    body["variables"] = device_variables.get()
    body["reinstallConfig1"] = True

    if await SmartEMS.edit_device(device_id, body):
        return config
    else:
        raise SEMSError(f"could not update device {device}", status_code=400)
