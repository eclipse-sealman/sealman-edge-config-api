from typing import Union

from fastapi import Depends

from authorization import resource_types as Resource
from authorization.permission_types import Device
from authorization.permission_check import PathParamPermissionCheck

from routers.base_api_router import BaseAPIRouter
from routers.module_config.schemas import GetModuleTwinIdentityResponse, GetModuleTwinBinaryResponse, GetModuleTwinResponse, ModuleList, ModuleConfStatus
from routers.module_config.config_schemas.network_discover_config_v1 import GetNetDiscoverModuleConfigV1, NetworkDiscoverModuleConfigV1
from routers.module_config.config_schemas.seal_module_opcua_client_config_v1 import OpcuaClientModuleConfigV1
from routers.module_config.routes.post_module_twin_config import post_module_twin_config as _post_module_twin_config
from routers.module_config.routes.get_module_twin_identity_reported import get_module_twin_identity_reported as _get_module_twin_identity_reported
from routers.module_config.routes.get_module_twin_config import get_module_twin_config as _get_module_twin_config
from routers.module_config.routes.get_module_twin_config_binary import get_module_twin_config_binary as _get_module_twin_config_binary
from routers.module_config.routes.post_module_config_status import (post_module_config_status
                                                                    as _post_module_config_status)

module_config = BaseAPIRouter()

# ============================================================
# POST Endpoints - Write Operations  
# ============================================================

@module_config.post("/{device}/twin/config/seal-app-opcua-client", tags=["Module Configuration"])
async def post_module_twin_config_opcua(device: str,
                                        request: OpcuaClientModuleConfigV1,
                                        _ = Depends(PathParamPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE, Resource.DEVICE, "device"))):
    return await _post_module_twin_config(device, "seal-app-opcua-client", request)

@module_config.post("/{device}/twin/config/seal-app-net-discover", tags=["Module Configuration"])
async def post_module_twin_config_discover(device: str, request: NetworkDiscoverModuleConfigV1,
                                  _ = Depends(PathParamPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE, Resource.DEVICE, "device"))):
    return await _post_module_twin_config(device, "seal-app-net-discover", request)

@module_config.post("/{device}/config/status", response_model=ModuleConfStatus,
                    tags=["Module Configuration"])
async def post_module_config_status(device: str, module_list: ModuleList,
                                    _ = Depends(PathParamPermissionCheck(Device.READ, Resource.DEVICE, "device"))):
    return await _post_module_config_status(device, module_list)

# ============================================================
# GET Endpoints - Read Operations
# ============================================================

@module_config.get("/{device}/twin/config/seal-app-net-discover", response_model=Union[GetNetDiscoverModuleConfigV1, None], response_model_exclude_none=True,
                   tags=["Module Configuration"])
async def get_net_discover_twin_config(device: str,
                                 _ = Depends(PathParamPermissionCheck(Device.READ, Resource.DEVICE, "device"))):
    return await _get_module_twin_config(device, "seal-app-net-discover")

@module_config.get("/{device}/twin/config/seal-app-opcua-client", response_model=Union[OpcuaClientModuleConfigV1, None], response_model_exclude_none=True,
                   tags=["Module Configuration"])
async def get_opcua_client_twin_config(device: str,
                                 _ = Depends(PathParamPermissionCheck(Device.READ, Resource.DEVICE, "device"))):
    return await _get_module_twin_config(device, "seal-app-opcua-client")

@module_config.get("/{device}/twin/config/{module}", response_model=Union[GetModuleTwinResponse, None],
                   tags=["Module Configuration"])
async def get_module_twin_config(device: str, module: str,
                                 _ = Depends(PathParamPermissionCheck(Device.READ, Resource.DEVICE, "device"))):
    return await _get_module_twin_config(device, module)


@module_config.get("/{device}/twin/config/{module}/binary", response_model=GetModuleTwinBinaryResponse,
                   tags=["Module Configuration"])
async def get_module_twin_config_binary(device: str, module: str,
                                        _ = Depends(PathParamPermissionCheck(Device.READ_MODULE_TWIN_CONFIG, Resource.DEVICE, "device"))):
    return await _get_module_twin_config_binary(device, module)

@module_config.get("/{device}/twin/identity/{module}/reported", response_model=GetModuleTwinIdentityResponse,
                   tags=["Module Configuration"])
async def get_module_twin_identity_reported(device: str, module: str,
                                 _ = Depends(PathParamPermissionCheck(Device.READ_MODULE_TWIN_CONFIG, Resource.DEVICE, "device"))):
    return await _get_module_twin_identity_reported(device, module)
