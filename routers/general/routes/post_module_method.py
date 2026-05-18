import asyncio
from auth import get_current_user
from helper import AuditTrail, get_iothub_auth_headers
from constants import IOT_HUB_NAME
from async_requests import post_async
from exceptions import IoTBackendAPIError


async def post_module_method(device: str, module: str, method_data, auth_context):
    """
        Post a module method on the selected device/module
    """
    responses = {}
    post_module_method_url = f"https://{IOT_HUB_NAME}/twins/{device}/modules/{module}" \
                             f"/methods?api-version=2020-05-31-preview"
    
    if hasattr(method_data, 'methodPayload') and method_data.methodPayload is not None:
        data = {
            "connectTimeoutInSeconds": 10,
            "methodName": method_data.methodName,
            "responseTimeoutInSeconds": 25,
            "payload": method_data.methodPayload
        }
    else:
        data = {
            "connectTimeoutInSeconds": 10,
            "methodName": method_data.methodName,
            "responseTimeoutInSeconds": 25,
        }

    headers = get_iothub_auth_headers()

    await asyncio.gather(
        post_async(post_module_method_url, responses, _json=data, headers=headers,
                   timeout=8),
        )

    resp = responses[post_module_method_url]
    if resp.status_code != 200:
        raise IoTBackendAPIError(resp.text, resp.status_code)

    # TODO: Check if this audit trail is needed and correct - use centralized logging with querying capabilities instead
    if auth_context is not None:
        await AuditTrail.log(
            get_current_user(auth_context),
            f"{auth_context.get('resource_type')}.{auth_context.get('permission')} ID:{auth_context.get('resource_id')}",
            method=f"{module}::{method_data.methodName}"
        )

    return resp.json()
