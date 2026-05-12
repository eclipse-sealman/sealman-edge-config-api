import asyncio
from constants import IOT_HUB_NAME
from async_requests import get_async, patch_async
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers
from routers.general.schemas import DeploymentTag


async def put_deployment_tag(device: str, deployment_tag: str) -> DeploymentTag:
    """
    This function updates the deployment tag of a device in IoT Hub. 
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
    
    # Check if device already has tags in IoT Hub
    if 'tags' in get_resp.json(): 
        current_device_tags = get_resp.json().get("tags")
    else: 
        # If the device does not have tags created on the IoT Hub, 
        # initialize with empty dict
        current_device_tags = {}
    
    current_device_tags["deployment"] = deployment_tag
    tags_patch = {"tags": current_device_tags}
    patch_device_twin_url = f"https://{IOT_HUB_NAME}/twins/{device}?api-version=2020-05-31-preview"
    await asyncio.gather(
        patch_async(patch_device_twin_url, responses, _json=tags_patch, headers=headers)
    )
    patch_resp = responses[patch_device_twin_url]

    if patch_resp.status_code != 200:
        raise IoTBackendAPIError(patch_resp.text, patch_resp.status_code)

    return DeploymentTag(deployment=deployment_tag)
