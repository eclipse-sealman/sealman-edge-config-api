import json
from helper import DeviceInfo, TemplateVariableType
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import SEMSError
from routers.smart_ems.schemas import PortForwardingConfig


async def post_smart_ems_config_port_forwarding(device: str, config: PortForwardingConfig):
    rules = config.get_rules()

    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)

    device_info.check_eligibility()
    device_id = device_info.get()["id"]

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

    port_forwarding_settings = {
        "rules": [
            {
                "name": rule.name,
                "interface": rule.interface,
                "srcPort": rule.srcPort,
                "destAddr": str(rule.destAddr),
                "destPort": rule.destPort
            }
            for rule in rules
        ]
    }

    device_info.upsert_variable(
        "port_forwarding_settings",
        json.dumps(port_forwarding_settings, separators=(",", ":")),
        TemplateVariableType.JSON_OBJECT,
    )

    body = generate_resp_from_device_info(device_info.get())
    body["reinstallConfig1"] = True

    if await SmartEMS.edit_device(device_id, body):
        return config
    else:
        raise SEMSError(f"could not update device {device}", status_code=400)
