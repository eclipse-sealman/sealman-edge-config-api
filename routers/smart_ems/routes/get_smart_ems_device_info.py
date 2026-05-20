from smart_ems import SmartEMS


async def get_smart_ems_device_info(device: str):
    smart_ems_device = await SmartEMS.get_device_by_serial(device)
    return get_smart_ems_device_data(smart_ems_device)


def get_smart_ems_device_data(smart_ems_device: dict):
    variables = []
    for var_obj in smart_ems_device.get("variables"):
        variables.append({
            "toString": var_obj["representation"],
            "variableType": var_obj["variableType"],
            "variableValue": var_obj["variableValue"],
        })

    template_name = "not assigned"
    template_exists = smart_ems_device.get("template") is not None
    template = smart_ems_device["template"].get("productionTemplate") if template_exists else None
    if template is not None:
        template_name = template.get("representation")
        
    device_type_id = smart_ems_device["deviceType"].get("id")
    device_type_name= smart_ems_device["deviceType"].get("name")

    resp = {
        "enabled": smart_ems_device.get("enabled"),
        "lastSeenAt": smart_ems_device.get("seenAt", "never seen"),
        "hardwareVersion": smart_ems_device.get("hardwareVersion", "unknown"),
        "updateFirmware": smart_ems_device.get("reinstallFirmware1"),
        "semsTemplate": "",
        "firmwareVersion": smart_ems_device.get("firmwareVersion1", "unknown"),
        "template": {
            "toString": template_name,
            "id": smart_ems_device.get("template").get("id") if template_exists else None,
            "createdAt": smart_ems_device.get("template").get("createdAt") if template_exists else None,
            "updatedAt": smart_ems_device.get("template").get("updatedAt") if template_exists else None
        },
        "deviceTypeId": device_type_id,
        "deviceTypeName": device_type_name,
        "description": smart_ems_device.get("description", ""),
        "variables": variables
    }

    return resp
