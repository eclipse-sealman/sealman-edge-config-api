from smart_ems import SmartEMS
from exceptions import UnmatchedDependency, SEMSError
from constants import LAN_EDGE_TEMPLATE_VERSIONS


def extract_device_variables(device_info):
    variables = []
    for var in device_info.get("variables"):
        variables.append(
            {
                "name": var.get("name"),
                "variableValue": var.get("variableValue")
             }
        )
    return variables


async def get_smart_ems_config_cellular(device: str):
    device_info = await SmartEMS.get_device_by_serial(device)
    device_id = device_info["id"]
    device_template = device_info["template"]["representation"]
    enabled_flag = device_info["enabled"]
    device_variables = extract_device_variables(device_info)

    cellular = False

    # check enabled status
    if not enabled_flag:
        raise UnmatchedDependency("the device needs to be enabled in smart-ems for this function", status_code=400)

    # check for supported template
    if device_template not in LAN_EDGE_TEMPLATE_VERSIONS:
        raise UnmatchedDependency(f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
                                  f" template. Currently <{device_template}> is in use", status_code=400)

    # check if device is capable of cellular
    for device_var in device_variables:
        if device_var["name"] == "cellular" and device_var["variableValue"] == "true":
            cellular = True
            break

    resp = {
        "cellular": cellular,
        "interface": {}
    }

    if cellular:
        config = await SmartEMS.device_config_download(device_id)
        network = config.get("network")
        if network is None:
            raise SEMSError(f"template <{device_template}> has invalid content "
                            f"-> cannot find any network configuration",
                            status_code=400)
        for interface in network:
            if "cellular" in interface:
                resp["interface"].update(network[interface])

    return resp
