from async_requests import get_async
from constants import IOT_HUB_NAME
from exceptions import IoTBackendAPIError
from helper import encode_iothub_id, get_iothub_auth_headers


async def get_module_twin_reported(device, module):
    """
        Returns the complete reported section of the module twin (without IoT Hub's `$metadata`),
        plus `lastUpdated` taken from it. Returns None if the module reported nothing yet.
    """
    url = f"https://{IOT_HUB_NAME}/twins/{encode_iothub_id(device)}/modules/{encode_iothub_id(module)}?api-version=2020-05-31-preview"
    responses = {}
    await get_async(url, responses, headers=get_iothub_auth_headers(), timeout=15)
    response = responses[url]
    if response.status_code == 404:
        raise IoTBackendAPIError(f"Module {module} not found on device {device}", 404)
    if response.status_code != 200:
        raise IoTBackendAPIError(f"Could not retrieve module twin: {response.text}", response.status_code)

    reported = response.json().get("properties", {}).get("reported")
    if not reported:
        return None
    result = {k: v for k, v in reported.items() if k != "$metadata"}
    last_updated = (reported.get("$metadata") or {}).get("$lastUpdated")
    if last_updated is not None:
        result["lastUpdated"] = last_updated
    return result
