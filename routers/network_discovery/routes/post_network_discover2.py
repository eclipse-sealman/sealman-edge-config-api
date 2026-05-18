from routers.general.routes.post_module_method import post_module_method
from routers.network_discovery.schemas import NetworkDiscover
from exceptions import EdgeModuleAPIError
from pydantic import BaseModel


class RequestDeviceModuleMethod(BaseModel):
    methodName: str
    methodPayload: dict


async def post_network_discover2(device: str, network_discover: NetworkDiscover, auth_context):
    module_name = "seal-app-net-discover"
    method_name = "scanNetwork2"
    method_payload = {
        "version": "1.0",
        "networkDefinition": network_discover.networkDefinition,
        "ports": network_discover.ports,
        "subnetMask": network_discover.subnetMask
    }
    method_data = RequestDeviceModuleMethod(methodName=method_name, methodPayload=method_payload)
    try:
        resp = await post_module_method(device, module_name, method_data, auth_context)
    except Exception:
        raise EdgeModuleAPIError(f"module {module_name} is not responding - please check if its deployed and connected "
                                 f"to the system", status_code=400)

    if resp.get("status") != 200:
        raise EdgeModuleAPIError(f"error while executing <{method_name}> on module <{module_name}>: "
                                 f"{resp.get('payload').get('message')}", status_code=resp.get("status"))

    return resp
