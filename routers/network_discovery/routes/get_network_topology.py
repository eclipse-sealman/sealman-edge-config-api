import asyncio
from constants import IOT_HUB_NAME
from async_requests import get_async
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers

NETWORK_DISCOVER_MODULE_NAME = "seal-app-net-discover"


async def get_network_topology(device: str):
    responses = {}
    url_twin_state = f"https://{IOT_HUB_NAME}/twins/{device}/modules/{NETWORK_DISCOVER_MODULE_NAME}?api-version=2020-05-31-preview"
    headers = get_iothub_auth_headers()
    await asyncio.gather(
        get_async(url_twin_state, responses, headers=headers),
    )

    response = {}
    if responses[url_twin_state].status_code == 200:
        data = responses[url_twin_state].json()
        if data['properties']['reported'].get("scanDefinition") is not None and data['properties']['reported'].get("scanResults") is not None:
            response['scanResults'] = data['properties']['reported']['scanResults']
            response['scanDefinition'] = data['properties']['reported']['scanDefinition']
        else:
            response = None
    else:
        raise IoTBackendAPIError(
            responses[url_twin_state].text, responses[url_twin_state].status_code)

    return response
