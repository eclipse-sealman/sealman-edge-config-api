from routers.smart_ems.schemas import LanInterface, SemsUpdateLan
from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import UnmatchedDependency, InvalidInputError
from constants import LAN_EDGE_TEMPLATE_VERSIONS


def check_template_compatibility(device_info):
    # check enabled status
    if not device_info["enabled"]:
        raise UnmatchedDependency("the device needs to be enabled in smart-ems for this function",
                                  status_code=400)

    # check for supported template
    if device_info["template"]["representation"] not in LAN_EDGE_TEMPLATE_VERSIONS:
        raise UnmatchedDependency(f"this function is only supported by devices using the "
                                  f"<{LAN_EDGE_TEMPLATE_VERSIONS}>"
                                  f" template. Currently <{device_info['template']['toString']}> is in use",
                                  status_code=400)


async def post_smart_ems_update_lan(device: str, request: SemsUpdateLan):
    device_info = await SmartEMS.get_device_by_serial(device)
    check_template_compatibility(device_info)
    device_id = device_info["id"]
    body = generate_resp_from_device_info(device_info)

    resp = {"updatedInterfaces": {}}
    interface_name_map = {"lan2": "lan_2", "lan3": "lan_3"}

    device_variables = body["variables"]

    device_variables_dict = {}
    for var_obj in device_variables:
        device_variables_dict.update({var_obj["name"]: var_obj["variableValue"]})

    def update_device_var(name, value):
        if value == "" or value is None:
            if name in device_variables_dict:
                device_variables_dict.pop(name)
        else:
            device_variables_dict.update({name: str(value)})

    def update_interface(if_name: str, interface: LanInterface):
        if_template_name = interface_name_map[if_name]

        # check if at least ip and subnet are set if dhcp is disabled
        if not interface.dhcp and (interface.ip is None or interface.subnet is None):
                raise InvalidInputError(f"for static network configuration of {if_name} at least valid values for "
                                        f"ip and subnet ar needed", 400)

        # configure interface
        if interface.dhcp:
            update_device_var(f"ip_{if_template_name}", None)
            update_device_var(f"subnet_{if_template_name}", None)
            update_device_var(f"gw_{if_template_name}", None)
            update_device_var(f"dns_{if_template_name}", None)
        else:
            update_device_var(f"ip_{if_template_name}", interface.ip)
            update_device_var(f"subnet_{if_template_name}", interface.subnet)
            update_device_var(f"gw_{if_template_name}", interface.gw)
            update_device_var(f"dns_{if_template_name}", interface.dns)

        # build static interface response
        interface_data = {}
        if interface.dhcp is not None:
            interface_data["dhcp"] = interface.dhcp
        if (interface.ip is not None):
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

    device_variables_updated = []
    for var_name in device_variables_dict:
        device_variables_updated.append({"name": var_name, "variableValue": device_variables_dict[var_name]})
    body["variables"] = device_variables_updated
    body["reinstallConfig1"] = True
    await SmartEMS.edit_device(device_id, body)

    return resp
