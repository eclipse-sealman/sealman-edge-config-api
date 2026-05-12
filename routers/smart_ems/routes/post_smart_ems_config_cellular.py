from smart_ems import SmartEMS, generate_resp_from_device_info
from exceptions import UnmatchedDependency, SEMSError
from constants import LAN_EDGE_TEMPLATE_VERSIONS
from ..schemas import CellularInterface


async def post_smart_ems_config_cellular(device: str, interface: CellularInterface):
    device_info = await SmartEMS.get_device_by_serial(device)

    # check enabled status
    if not device_info["enabled"]:
        raise UnmatchedDependency("the device needs to be enabled in smart-ems for this function",
                                  status_code=400)

    # check for supported template
    if device_info["template"]["representation"] not in LAN_EDGE_TEMPLATE_VERSIONS:
        raise UnmatchedDependency(f"this function is only supported by devices using "
                                  f"the <{LAN_EDGE_TEMPLATE_VERSIONS}> template. Currently "
                                  f"<{device_info['template']['representation']}> is in use",
                                  status_code=400)

    device_id = device_info["id"]

    body = generate_resp_from_device_info(device_info)
    device_variables = body["variables"]

    device_variables_dict = {}
    for var_obj in device_variables:
        device_variables_dict.update({var_obj["name"]: var_obj["variableValue"]})

    def add_device_var(name, value):
        if value == "" or value is None:
            if name in device_variables_dict:
                device_variables_dict.pop(name)
        else:
            device_variables_dict.update({name: value})

    interface = interface.model_dump()

    add_device_var("cellular_apn", interface.get("apn", ""))
    add_device_var("cellular_pin", interface.get("pin", ""))
    add_device_var("cellular_access_number", interface.get("access_number", ""))
    add_device_var("cellular_auth_method", interface.get("auth_method", ""))
    add_device_var("cellular_user", interface.get("username", ""))
    add_device_var("cellular_pw", interface.get("password", ""))
    add_device_var("cellular_state", interface.get("state", "off"))

    device_variables_updated = []
    for name in device_variables_dict:
        device_variables_updated.append({"name": name, "variableValue": device_variables_dict[name]})
    body["variables"] = device_variables_updated
    body["reinstallConfig1"] = True
    if await SmartEMS.edit_device(device_id, body):
        return interface
    else:
        raise SEMSError(f"could not update device {device}", status_code=400)
