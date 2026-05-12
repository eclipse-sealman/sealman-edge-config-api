import asyncio
from constants import IOT_HUB_NAME
from async_requests import get_async
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers
from routers.general.schemas import DeploymentTag


async def get_deployment_tag(device: str) -> DeploymentTag:
    """
    This function retrieves the deployment tag of a device in IoT Hub. 
    """
    responses = {}
    get_device_twin_url = f"https://{IOT_HUB_NAME}/twins/{device}?api-version=2020-05-31-preview"
    headers = get_iothub_auth_headers()
    await asyncio.gather(
        get_async(get_device_twin_url, responses, headers=headers),
    )
    get_resp = responses[get_device_twin_url]
    
    if get_resp.status_code != 200:
        raise IoTBackendAPIError(get_resp.text, get_resp.status_code)
    
    deployment_tag = get_resp.json().get("tags", {}).get("deployment", None)
    return DeploymentTag(deployment=deployment_tag)
