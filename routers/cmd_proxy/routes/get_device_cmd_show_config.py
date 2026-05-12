from routers.general.routes.post_module_method import post_module_method
from exceptions import EdgeModuleAPIError
from pydantic import BaseModel


class RequestDeviceModuleMethod(BaseModel):
    methodName: str
    methodPayload: dict


async def get_device_cmd_show_config(device: str, auth_context):
    module_name = "seal-app-cmd-proxy"
    module_method_name = "device_show"
    method_payload = {
        "version": "1.0"
    }
    method_data = RequestDeviceModuleMethod(methodName=module_method_name, methodPayload=method_payload)
    resp = await post_module_method(device, module=module_name, method_data=method_data,
                                    auth_context=auth_context)

    if resp.get("status") != 200:
        raise EdgeModuleAPIError(f"error while executing <{module_method_name}> on module <{module_name}>: "
                                 f"{resp.get('payload').get('message')}", status_code=resp.get("status"))
    return resp.get("payload")
