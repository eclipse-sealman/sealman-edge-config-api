from authorization import resource_types as Resource
from authorization.permission_check import PathParamPermissionCheck
from authorization.permission_types import Device
from fastapi import Depends

from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter

from .routes.get_smart_ems_device_info import get_smart_ems_device_info as _get_smart_ems_device_info
from .routes.get_smart_ems_firmware_status import get_smart_ems_firmware_status as _get_smart_ems_firmware_status
from .routes.post_smart_ems_update_lan import post_smart_ems_update_lan as _post_smart_ems_update_lan
from .routes.get_smart_ems_lan import get_smart_ems_lan as _get_smart_ems_lan
from .routes.post_smart_ems_config_cellular import post_smart_ems_config_cellular as _post_smart_ems_config_cellular
from .routes.get_smart_ems_config_cellular import get_smart_ems_config_cellular as _get_smart_ems_config_cellular
from .routes.get_smart_ems_device_config import get_smart_ems_device_config as _get_smart_ems_device_config
from .routes.post_smart_ems_config_nat import post_smart_ems_config_nat as _post_smart_ems_config_nat
from .routes.get_smart_ems_config_nat import get_smart_ems_config_nat as _get_smart_ems_config_nat
from .routes.get_smart_ems_secret_info import get_smart_ems_secret_info as _get_smart_ems_secret_info
from .routes.post_smart_ems_device_secret_request import post_smart_ems_device_secret_request as _post_smart_ems_device_secret_request
from .routes.post_smart_ems_device_secret_renew import post_smart_ems_device_secret_renew as _post_smart_ems_device_secret_renew
from .routes.get_smart_ems_default_template import get_smart_ems_default_template as _get_smart_ems_default_template
from .routes.post_smart_ems_apply_default_template import post_smart_ems_apply_default_template as _post_smart_ems_apply_default_template
from .schemas import (DeviceSecretInformation, DeviceSecretValue, SemsInfo2, SemsFirmwareStatus,
                      SemsUpdateLan, SemsUpdateLanRes, SemsGetLan, ConfigCellular, CellularInterface,
                      GeneratedDeviceConfig, NatConfig, DefaultSmartEMSTemplate, ApplyDefaultTemplateResult)
from .routes.post_smart_ems_config_port_forwarding import post_smart_ems_config_port_forwarding
from .routes.get_smart_ems_config_port_forwarding import get_smart_ems_config_port_forwarding
from .schemas import PortForwardingConfig


smart_ems = BaseAPIRouter()


@smart_ems.get("/{device}/smartems/info", response_model=SemsInfo2, tags=["Smart-EMS"])
async def get_smart_ems_device_info(device: str,
                                    _ = Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_DEVICE_INFO, Resource.DEVICE, "device"))):
    return await _get_smart_ems_device_info(device)


@smart_ems.get("/{device}/smartems/status", response_model=SemsFirmwareStatus, tags=["Smart-EMS"])
async def get_smart_ems_firmware_status(device: str,
                                        _ = Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_FIRMWARE_STATUS, Resource.DEVICE, "device"))):
    return await _get_smart_ems_firmware_status(device)


@smart_ems.post("/{device}/smartems/configure/lan", response_model=SemsUpdateLanRes, tags=["Smart-EMS"])
async def post_smart_ems_update_lan(device: str, lan_interface_conf: SemsUpdateLan,
                                    _ = Depends(PathParamPermissionCheck(Device.EDIT_SMARTEMS_CONFIG_LAN, Resource.DEVICE, "device"))):
    return await _post_smart_ems_update_lan(device, lan_interface_conf)


@smart_ems.get("/{device}/smartems/config/lan", response_model=SemsGetLan, tags=["Smart-EMS"])
async def get_smart_ems_config_lan(device: str,
                                   _ = Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_CONFIG_LAN, Resource.DEVICE, "device"))):
    return await _get_smart_ems_lan(device)


@smart_ems.get("/{device}/smartems/config/cellular", response_model=ConfigCellular, tags=["Smart-EMS"])
async def get_smart_ems_config_cellular(device: str,
                                        _ = Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_CONFIG_CELLULAR, Resource.DEVICE, "device"))):
    return await _get_smart_ems_config_cellular(device)


@smart_ems.post("/{device}/smartems/config/cellular", response_model=CellularInterface, tags=["Smart-EMS"])
async def post_smart_ems_config_cellular(device: str, cellular_interface: CellularInterface,
                                         _ = Depends(PathParamPermissionCheck(Device.EDIT_SMARTEMS_CONFIG_CELLULAR, Resource.DEVICE, "device"))):
    return await _post_smart_ems_config_cellular(device, cellular_interface)


@smart_ems.get("/{device}/smartems/config/export", response_model=GeneratedDeviceConfig, tags=["Smart-EMS"])
async def get_smart_ems_device_config(device: str,
                                      _ = Depends(PathParamPermissionCheck(Device.EXPORT_SMARTEMS_CONFIG, Resource.DEVICE, "device"))):
    return await _get_smart_ems_device_config(device)


@smart_ems.get("/{device}/smartems/config/nat", response_model=NatConfig, tags=["Smart-EMS"])
async def get_smart_ems_config_nat(device: str,
                                   _ = Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_CONFIG_NAT, Resource.DEVICE, "device"))):
    return await _get_smart_ems_config_nat(device)


@smart_ems.post("/{device}/smartems/config/nat", response_model=NatConfig, tags=["Smart-EMS"])
async def post_smart_ems_config_nat(device: str, nat_config: NatConfig,
                                    _ = Depends(PathParamPermissionCheck(Device.EDIT_SMARTEMS_CONFIG_NAT, Resource.DEVICE, "device"))):
    return await _post_smart_ems_config_nat(device, nat_config)


@smart_ems.get("/{device}/smartems/secret/info", response_model=DeviceSecretInformation, tags=["Smart-EMS"])
async def get_smart_ems_secret_info(device: str, 
                                    _ = Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_DEVICE_INFO, Resource.DEVICE, "device"))):
    
    return await _get_smart_ems_secret_info(device)


@smart_ems.post("/{device}/smartems/secret/renew", tags=["Smart-EMS"])
async def post_smart_ems_secret_renew(device: str, 
                                renew_task_repo: PasswordRenewalTaskRepository = Depends(get_repository(PasswordRenewalTaskRepository)),
                                _ = Depends(PathParamPermissionCheck(Device.EDIT_PASSWORD, Resource.DEVICE, "device"))):
    
    return await _post_smart_ems_device_secret_renew(device, renew_task_repo)


@smart_ems.post("/{device}/smartems/secret/request", response_model=DeviceSecretValue, tags=["Smart-EMS"])
async def post_smart_ems_secret_request(device: str, 
                                renew_task_repo: PasswordRenewalTaskRepository = Depends(get_repository(PasswordRenewalTaskRepository)),
                                _ = Depends(PathParamPermissionCheck(Device.READ_PASSWORD, Resource.DEVICE, "device"))):
    
    return await _post_smart_ems_device_secret_request(device, renew_task_repo)

@smart_ems.get("/smartems/default-template", response_model=DefaultSmartEMSTemplate, tags=["Smart-EMS"])
async def get_smart_ems_default_template(device_type: str, hardware_version: str):

    return await _get_smart_ems_default_template(device_type, hardware_version)

@smart_ems.post("/{device}/smartems/apply-default-template", response_model=ApplyDefaultTemplateResult, tags=["Smart-EMS"])
async def post_smart_ems_apply_default_template(device: str,
                                _ = Depends(PathParamPermissionCheck(Device.APPLY_SMARTEMS_TEMPLATE, Resource.DEVICE, "device"))):

    return await _post_smart_ems_apply_default_template(device)
    
@smart_ems.post("/{device}/smartems/config/port-forwarding", response_model=PortForwardingConfig, tags=["Smart-EMS"])
async def set_port_forwarding(device: str, config: PortForwardingConfig,
                                   _=Depends(PathParamPermissionCheck(Device.EDIT_SMARTEMS_CONFIG_NAT,Resource.DEVICE,"device"))):
    return await post_smart_ems_config_port_forwarding(device, config)


@smart_ems.get("/{device}/smartems/config/port-forwarding", response_model=PortForwardingConfig, tags=["Smart-EMS"])
async def get_port_forwarding(device: str, 
                                   _=Depends(PathParamPermissionCheck(Device.READ_SMARTEMS_CONFIG_NAT,Resource.DEVICE,"device"))):
    return await get_smart_ems_config_port_forwarding(device)
