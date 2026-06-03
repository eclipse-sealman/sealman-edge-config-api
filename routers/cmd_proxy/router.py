from fastapi import Depends

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from routers.base_api_router import BaseAPIRouter
from .routes.post_device_cmd_set_ip_static import post_device_cmd_set_ip_static as _post_device_cmd_set_ip_static
from .routes.get_device_cmd_ip_config import get_device_cmd_ip_config as _get_device_cmd_ip_config
from .routes.post_device_cmd_smartems_check import post_device_cmd_smartems_check as _post_device_cmd_smartems_check
from .routes.get_device_cmd_status import get_device_module_status as _get_device_module_status
from .routes.get_device_cmd_fw_config import get_device_cmd_fw_config as _get_device_cmd_fw_config
from .routes.get_device_cmd_show_config import get_device_cmd_show_config as _get_device_cmd_show_config
from .schemas import DeviceModuleStatus, NMShow, FWShow, SEMSCheck, LanIpStatic


cmd_proxy = BaseAPIRouter()


@cmd_proxy.get("/{device}/cmd/status", response_model=DeviceModuleStatus, tags=["CMD Proxy"])
async def get_device_cmd_status(device: str,
                                _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_device_module_status(device, "seal-app-cmd-proxy")


@cmd_proxy.get("/{device}/cmd/nm/config", response_model=NMShow, tags=["CMD Proxy"])
async def get_device_ip_config(device: str,
                                auth_context = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_device_cmd_ip_config(device, auth_context)


@cmd_proxy.post("/{device}/cmd/nm/set/static", response_model=NMShow, tags=["CMD Proxy"])
async def post_device_set_ip_static(device: str, ip_static: LanIpStatic,
                                    auth_context = Depends(ABACPermissionCheck(Device.NETWORK_WRITE))):
    return await _post_device_cmd_set_ip_static(device, ip_static, auth_context)


@cmd_proxy.get("/{device}/cmd/fw/config", response_model=FWShow, tags=["CMD Proxy"])
async def get_device_fw_config(device: str,
                               auth_context = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_device_cmd_fw_config(device, auth_context)


@cmd_proxy.post("/{device}/cmd/smartems/check", response_model=SEMSCheck, tags=["CMD Proxy"])
async def post_device_smartems_check(device: str,
                                     auth_context = Depends(ABACPermissionCheck(Device.READ))):
    return await _post_device_cmd_smartems_check(device, auth_context)


# TODO: response model is extremely huge and not all optional keys are fully known since lack of doc at welotec side
@cmd_proxy.get("/{device}/cmd/config/show", tags=["CMD Proxy"])
async def get_device_config_show(device: str,
                                 auth_context = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_device_cmd_show_config(device, auth_context)
