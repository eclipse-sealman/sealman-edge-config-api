from datetime import datetime
from constants import DEVICE_AUTHENTICATION_SECRET_KEY
from exceptions import SEMSFirmwareError
from routers.smart_ems.schemas import DeviceSecretInformation
from smart_ems import SmartEMS


async def get_smart_ems_secret_info(device: str):
    device_secret_info = DeviceSecretInformation(
        deviceTypeHasAuthSecret=False, secretCreatedAt=None, secretUpdatedAt=None, error=None
    )
    try:
        secret = await SmartEMS.get_device_secret(device, DEVICE_AUTHENTICATION_SECRET_KEY)
    except SEMSFirmwareError as e:
        device_secret_info.error = str(e)
        return device_secret_info

    if (secret is not None):
        device_secret_info.deviceTypeHasAuthSecret = True
        device_type_secret = secret.get("deviceTypeSecret")
        device_secret_info.secretValueRenewAfterDays = device_type_secret.get(
            "secretValueRenewAfterDays"
        )
        device_secret_info.forceRenewal = secret.get("forceRenewal", False)

        device_secret_info.id = secret.get("id")
        if device_secret_info.id is not None:
            device_secret_info.secretCreatedAt = datetime.fromisoformat(
                secret.get("createdAt")
            )
            device_secret_info.secretUpdatedAt = (
                datetime.fromisoformat(secret["updatedAt"])
                if secret.get("updatedAt") is not None
                else None
            )
                
    return device_secret_info
