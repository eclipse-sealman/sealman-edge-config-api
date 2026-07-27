import asyncio
import json
from helper import AuditTrail, get_iothub_auth_headers
from constants import IOT_HUB_NAME
from async_requests import post_async
from exceptions import IoTBackendAPIError


async def post_module_method(device: str, module: str, method_data, auth_context):
    """
    Post a module method on the selected device/module
    """
    responses = {}
    post_module_method_url = (
        f"https://{IOT_HUB_NAME}/twins/{device}/modules/{module}"
        f"/methods?api-version=2020-05-31-preview"
    )

    connect_timeout_seconds = 10
    response_timeout_seconds = 25

    if hasattr(method_data, "methodPayload") and method_data.methodPayload is not None:
        data = {
            "connectTimeoutInSeconds": connect_timeout_seconds,
            "methodName": method_data.methodName,
            "responseTimeoutInSeconds": response_timeout_seconds,
            "payload": method_data.methodPayload,
        }
    else:
        data = {
            "connectTimeoutInSeconds": connect_timeout_seconds,
            "methodName": method_data.methodName,
            "responseTimeoutInSeconds": response_timeout_seconds,
        }

    headers = get_iothub_auth_headers()

    # Our own HTTP timeout must exceed how long we tell IoT Hub to wait for the device
    # (connect + response timeout above), otherwise we give up before IoT Hub does and
    # misreport a healthy-but-slow device/module as "not responding". Add a margin for
    # network/relay overhead on top. Restarts get extra headroom since the module needs
    # time to actually reboot before it can respond.
    default_timeout = connect_timeout_seconds + response_timeout_seconds + 10
    timeout = 60 if "restart" in method_data.methodName.lower() else default_timeout

    await asyncio.gather(
        post_async(
            post_module_method_url,
            responses,
            _json=data,
            headers=headers,
            timeout=timeout,
        ),
    )

    resp = responses[post_module_method_url]
    if resp.status_code != 200:
        raise IoTBackendAPIError(resp.text, resp.status_code)

    try:
        resp_json = resp.json()
    except ValueError:
        resp_json = None

    def build_error_message():
        if not isinstance(resp_json, dict):
            return resp.text

        payload = resp_json.get("payload")
        if isinstance(payload, dict):
            message = payload.get("message", resp.text)
            detail = payload.get("detail")
        else:
            message = resp_json.get("message", resp.text)
            detail = resp_json.get("detail")

        if detail is None:
            return message

        return json.dumps({"message": message, "detail": detail})

    # Direct method may return HTTP 200 but embed an application error status in body.
    body_status = resp_json.get("status") if isinstance(resp_json, dict) else None
    has_body_error = (
        isinstance(body_status, bool)
        and not body_status
        or isinstance(body_status, int)
        and body_status != 200
    )
    if has_body_error:
        error_status = body_status if isinstance(body_status, int) else resp.status_code
        raise IoTBackendAPIError(build_error_message(), error_status)

    # TODO: Check if this audit trail is needed and correct - use centralized logging with querying capabilities instead
    if auth_context is not None:
        # auth_context here is an ABACPermissionCheckResult ({user_name, user_id, permission,
        # device_id}), not the raw JWT claims - it already carries the resolved user identity,
        # so there's no need (and no correct way) to re-derive it via get_current_user().
        await AuditTrail.log(
            auth_context.get("user_name"),
            f"{auth_context.get('permission')} ID:{auth_context.get('device_id')}",
            method=f"{module}::{method_data.methodName}",
        )

    return resp_json
