from smart_ems import SmartEMS
from exceptions import UnmatchedDependency
from constants import LAN_EDGE_TEMPLATE_VERSIONS
from ..schemas import GeneratedDeviceConfig


async def get_smart_ems_device_config(device: str):
    # read current device info
    device_info = await SmartEMS.get_device_by_serial(device)
    device_id = device_info["id"]
    device_template = device_info["template"]["representation"]
    enabled_flag = device_info["enabled"]

    # check enabled status
    if not enabled_flag:
        raise UnmatchedDependency("the device needs to be enabled in smart-ems for this function", status_code=400)

    # check for supported template
    if device_template not in LAN_EDGE_TEMPLATE_VERSIONS:
        raise UnmatchedDependency(f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
                                  f" template. Currently <{device_template}> is in use", status_code=400)

    config = await SmartEMS.device_config_download(device_id)

    return GeneratedDeviceConfig(config)
