import logging
from typing import Any, Dict
from fastapi import Depends
from authorization.permission_check import EntityLookup, PathParamPermissionCheck, PermissionCheck
from authorization import resource_types as Resource
from authorization.permission_types import Device, Platform
from constants import AUTHORIZATION_API_PLATFORM_NAME
from db.repos.device import DeviceRepository
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter
from routers.general.schemas import (
    DeploymentTag,
    DeviceMetadataResponse,
    DeviceStatusWithConnectionList,
    ResponseDeploymentList,
    DeviceModuleList,
    DeviceConnectionStatus,
    DeviceModuleMethodReq,
    ModuleDeploymentStatus,
)
from common_schemas import DirectMethod
from .routes.get_device_metadata import get_device_metadata as _get_device_metadata
from .routes.patch_device_metadata import patch_device_metadata as _patch_device_metadata
from .routes.get_device_modules import get_device_modules as _get_device_modules
from .routes.get_deployments_list import get_deployment_list as _get_deployment_list
from .routes.get_deployment_tag import get_deployment_tag as _get_deployment_tag
from .routes.put_deployment_tag import put_deployment_tag as _put_deployment_tag
from .routes.get_device_connection_status import get_device_connection_status as _get_device_connection_status
from .routes.post_module_method import post_module_method as _post_module_method
from .routes.get_module_deployment_status import get_module_deployment_status as _get_module_deployment_status

general = BaseAPIRouter()
logger = logging.getLogger("EdgeConfigAPI")


# ============================================================
# PATCH Endpoints - Partial Update Operations
# ============================================================

@general.patch("/{device}/metadata", response_model=DeviceMetadataResponse, tags=["General"])
async def patch_device_metadata(device: str, metadata: Dict[str, Any],
                                repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
                                _ = Depends(PathParamPermissionCheck(Device.READ, Resource.DEVICE, "device"))):
    device = await _patch_device_metadata(device, metadata=metadata, repo=repo)
    return device

@general.put("/{device}/deployment", response_model=DeploymentTag, tags=["General"])
async def put_deployment_tag(device: str, deployment: DeploymentTag,
                           _ = Depends(PathParamPermissionCheck(Device.EDIT_DEPLOYMENT_TAG, Resource.DEVICE, "device"))):
    return await _put_deployment_tag(device, deployment.deployment)

# ============================================================
# POST Endpoints - Write Operations  
# ============================================================

@general.post("/{device}/{module}/methods", response_model=DirectMethod[Any], tags=["General"])
async def post_module_method(device: str, module: str, method_data: DeviceModuleMethodReq,
                             auth_context = Depends(PathParamPermissionCheck(Device.EXECUTE_MODULE_METHOD, Resource.DEVICE, "device"))):
    return await _post_module_method(device, module, method_data, auth_context)


# ============================================================
# GET Endpoints - Read Operations
# ============================================================

@general.get("/{device}/metadata", response_model=DeviceMetadataResponse, tags=["General"])
async def get_device_metadata(device: str, repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
                              _=Depends(PathParamPermissionCheck(Device.READ, Resource.DEVICE, "device"))):
    device = await _get_device_metadata(device, repo=repo)
    return device

@general.get("/{device}/deployment", response_model=DeploymentTag, tags=["General"])
async def get_deployment_tag(device: str,
                           _ = Depends(PathParamPermissionCheck(Device.READ_DEPLOYMENT_TAG, Resource.DEVICE, "device"))):
    return await _get_deployment_tag(device)

@general.get("/deployments", response_model=ResponseDeploymentList, tags=["General"])
async def get_deployment_list(_ = Depends(PermissionCheck(Platform.READ_DEPLOYMENT_LIST, Resource.PLATFORM, AUTHORIZATION_API_PLATFORM_NAME))):
    return await _get_deployment_list()

@general.get("/{device}/modules", response_model=DeviceModuleList, tags=["General"])
async def get_device_modules(device,
                             _ = Depends(PathParamPermissionCheck(Device.READ_MODULES, Resource.DEVICE, "device"))):
    return await _get_device_modules(device)

@general.get("/{device}/connection/status", response_model=DeviceConnectionStatus, tags=["General"])
async def get_device_connection_status(device: str,
                                       _ = Depends(PathParamPermissionCheck(Device.READ_CONNECTION_STATUS, Resource.DEVICE, "device"))):
    return await _get_device_connection_status(device)

@general.get("/{device}/deployment/status", response_model=ModuleDeploymentStatus, tags=["General"])
async def get_module_deployment_status(device: str,  
                                       _ = Depends(PathParamPermissionCheck(Device.READ_MODULE_DEPLOYMENT_STATUS, Resource.DEVICE, "device"))):
    return await _get_module_deployment_status(device)