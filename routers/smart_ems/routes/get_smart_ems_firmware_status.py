from smart_ems import SmartEMS


async def get_smart_ems_firmware_status(device: str):
    return await SmartEMS.get_firmware_update_status(device)
