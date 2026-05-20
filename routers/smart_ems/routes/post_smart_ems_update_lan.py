import json
from routers.smart_ems.schemas import LanInterface, SemsUpdateLan
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import InvalidInputError
from helper import DeviceInfo, TemplateVariableType


async def post_smart_ems_update_lan(device: str, request: SemsUpdateLan):
    device_by_serial = await SmartEMS.get_device_by_serial(device)
    device_info = DeviceInfo(device_by_serial)
    device_info.check_eligibility()
    device_id = device_info.get()["id"]

    resp = {"updatedInterfaces": {}}

    lan_settings_raw = device_info.get_variable_value("lan_settings")
    try:
        lan_settings = json.loads(lan_settings_raw) if lan_settings_raw else {}
    except (TypeError, ValueError):
        lan_settings = {}

    if not isinstance(lan_settings, dict):
        lan_settings = {}

    for interface_name in ("lan2", "lan3"):
        if not isinstance(lan_settings.get(interface_name), dict):
            lan_settings[interface_name] = {
                "dhcp": True,
                "ip": [],
                "subnet": [],
                "gateway": None,
                "dns": None,
            }

    def update_interface(if_name: str, interface: LanInterface):
        # check if at least ip and subnet are set if dhcp is disabled
        if not interface.dhcp and (interface.ip is None or interface.subnet is None):
                raise InvalidInputError(f"for static network configuration of {if_name} at least valid values for "
                                        f"ip and subnet are needed", 400)

        if interface.dhcp:
            lan_settings[if_name] = {
                "dhcp": True,
                "ip": [],
                "subnet": [],
                "gateway": None,
                "dns": None,
            }
        else:
            lan_settings[if_name] = {
                "dhcp": False,
                "ip": [str(interface.ip)] if interface.ip is not None else [],
                "subnet": [str(interface.subnet)] if interface.subnet is not None else [],
                "gateway": str(interface.gw) if interface.gw is not None else None,
                "dns": str(interface.dns) if interface.dns is not None else None,
            }

        # build static interface response
        interface_data = {}
        if interface.dhcp is not None:
            interface_data["dhcp"] = interface.dhcp
        if interface.ip is not None:
            interface_data["ip"] = interface.ip
        if interface.subnet is not None:
            interface_data["subnet"] = interface.subnet
        if interface.gw is not None:
            interface_data["gateway"] = interface.gw
        if interface.dns is not None:
            interface_data["dns"] = interface.dns

        resp["updatedInterfaces"].update({if_name: interface_data})

    if request.lan2 is not None:
        update_interface("lan2", request.lan2)
    if request.lan3 is not None:
        update_interface("lan3", request.lan3)

    device_info.upsert_variable(
        "lan_settings",
        json.dumps(lan_settings, separators=(",", ":")),
        TemplateVariableType.JSON_OBJECT,
    )

    body = generate_resp_from_device_info(device_info.get())
    body["reinstallConfig1"] = True
    await SmartEMS.edit_device(device_id, body)

    return resp
