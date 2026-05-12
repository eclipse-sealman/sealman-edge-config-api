import asyncio
from constants import IOT_HUB_NAME
from async_requests import get_async
from exceptions import ModuleNotFound, IoTBackendAPIError
from helper import get_iothub_auth_headers


async def get_device_module_status(device: str, module: str):
    responses = {}
    url1 = f"https://{IOT_HUB_NAME}/devices/{device}/modules?api-version=2020-05-31-preview"
    headers = get_iothub_auth_headers()

    await asyncio.gather(
        get_async(url1, responses, headers=headers)
    )

    if responses[url1].status_code != 200:
        raise IoTBackendAPIError(f"error while executing backend API {url1}", status_code=responses[url1].status_code)

    module_status = "undefined"
    module_available = False
    for modules in responses[url1].json():
        if modules["moduleId"] == module:
            module_status = modules["connectionState"]  # returns 'Connected' or 'Disconnected'
            module_available = True
            break

    if not module_available:
        raise ModuleNotFound(f"module {module} is not deployed on device {device}", 404)

    return {"moduleName": module,
            "moduleStatus": module_status}
