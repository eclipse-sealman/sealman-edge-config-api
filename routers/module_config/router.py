from typing import Any, Dict, Union

from fastapi import Body, Depends, HTTPException

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from db.repos.field_file import FieldFileRepository
from db.repos.module_config import ModuleConfigRepository
from db.session import get_repository

from routers.base_api_router import BaseAPIRouter
from routers.module_config.schemas import GetModuleTwinIdentityResponse, GetModuleTwinBinaryResponse, GetModuleTwinResponse, ModuleList, ModuleConfStatus
from routers.module_config.config_schemas.network_discover_config_v1 import GetNetDiscoverModuleConfigV1, NetworkDiscoverModuleConfigV1
from routers.module_config.config_schemas.seal_module_opcua_client_config_v1 import OpcuaClientModuleConfigV1
from routers.module_config.routes.post_module_twin_config import post_module_twin_config as _post_module_twin_config
from routers.module_config.routes.get_module_twin_reported import get_module_twin_reported as _get_module_twin_reported
from routers.module_config.routes.get_module_twin_identity_reported import get_module_twin_identity_reported as _get_module_twin_identity_reported
from routers.module_config.routes.get_module_twin_config import get_module_twin_config as _get_module_twin_config
from routers.module_config.routes.get_module_twin_config_binary import get_module_twin_config_binary as _get_module_twin_config_binary
from routers.module_config.secrets import SecretRestoreError
from routers.module_config.service import write_supplied_config
from routers.module_config.routes.post_module_config_status import (post_module_config_status
                                                                    as _post_module_config_status)

module_config = BaseAPIRouter()
_revisions = Depends(get_repository(ModuleConfigRepository))
_files = Depends(get_repository(FieldFileRepository))

# ============================================================
# POST Endpoints - Write Operations  
# ============================================================

@module_config.post("/{device}/twin/config/seal-module-opcua-client", tags=["Module Configuration"])
async def post_module_twin_config_opcua(device: str,
                                        request: OpcuaClientModuleConfigV1,
                                        auth = Depends(ABACPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE)),
                                        repo: ModuleConfigRepository = _revisions,
                                        files_repo: FieldFileRepository = _files):
    try:
        return await _post_module_twin_config(device, "seal-module-opcua-client", request, actor=auth["user_name"],
                                              repo=repo, files_repo=files_repo)
    except SecretRestoreError as exc:
        raise HTTPException(409, str(exc))

@module_config.post("/{device}/twin/config/seal-app-net-discover", tags=["Module Configuration"])
async def post_module_twin_config_discover(device: str, request: NetworkDiscoverModuleConfigV1,
                                  auth = Depends(ABACPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE)),
                                  repo: ModuleConfigRepository = _revisions,
                                  files_repo: FieldFileRepository = _files):
    try:
        return await _post_module_twin_config(device, "seal-app-net-discover", request, actor=auth["user_name"],
                                              repo=repo, files_repo=files_repo)
    except SecretRestoreError as exc:
        raise HTTPException(409, str(exc))

# Registered after the typed endpoints above, which keep validating their modules' schemas.
@module_config.post("/{device}/twin/config/{module}", tags=["Module Configuration"])
async def post_module_twin_config_raw(device: str, module: str, config: Dict[str, Any] = Body(...),
                                      auth = Depends(ABACPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE)),
                                      repo: ModuleConfigRepository = _revisions,
                                      files_repo: FieldFileRepository = _files):
    """Writes any JSON object as the module configuration (raw editor) and records a revision.
    A mask of the current revision is written as that file's current content."""
    try:
        result = await write_supplied_config(device, module, config, source="raw", actor=auth["user_name"],
                                             repo=repo, files_repo=files_repo)
    except SecretRestoreError as exc:
        raise HTTPException(409, str(exc))
    revision = result["revision"]
    return {"configId": result["configId"], "revisionId": revision["id"] if revision else None}

@module_config.post("/{device}/config/status", response_model=ModuleConfStatus,
                    tags=["Module Configuration"])
async def post_module_config_status(device: str, module_list: ModuleList,
                                    _ = Depends(ABACPermissionCheck(Device.READ))):
    resps = await _post_module_config_status(device, module_list)
    return resps

# ============================================================
# GET Endpoints - Read Operations
# ============================================================

@module_config.get("/{device}/twin/config/seal-app-net-discover", response_model=Union[GetNetDiscoverModuleConfigV1, None], response_model_exclude_none=True,
                   tags=["Module Configuration"])
async def get_net_discover_twin_config(device: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ)),
                                 repo: ModuleConfigRepository = _revisions):
    return await _get_module_twin_config(device, "seal-app-net-discover", repo)

@module_config.get("/{device}/twin/config/seal-module-opcua-client", response_model=Union[OpcuaClientModuleConfigV1, None], response_model_exclude_none=True,
                   tags=["Module Configuration"])
async def get_opcua_client_twin_config(device: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ)),
                                 repo: ModuleConfigRepository = _revisions):
    return await _get_module_twin_config(device, "seal-module-opcua-client", repo)

@module_config.get("/{device}/twin/config/{module}", response_model=Union[GetModuleTwinResponse, None],
                   tags=["Module Configuration"])
async def get_module_twin_config(device: str, module: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ)),
                                 repo: ModuleConfigRepository = _revisions):
    """The current configuration; values that came from a secret file are masked."""
    return await _get_module_twin_config(device, module, repo)


@module_config.get("/{device}/twin/config/{module}/binary", response_model=GetModuleTwinBinaryResponse,
                   tags=["Module Configuration"])
async def get_module_twin_config_binary(device: str, module: str,
                                        _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_module_twin_config_binary(device, module)

@module_config.get("/{device}/twin/identity/{module}/reported", response_model=GetModuleTwinIdentityResponse,
                   tags=["Module Configuration"])
async def get_module_twin_identity_reported(device: str, module: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_module_twin_identity_reported(device, module)

@module_config.get("/{device}/twin/reported/{module}", response_model=GetModuleTwinResponse,
                   tags=["Module Configuration"])
async def get_module_twin_reported_full(device: str, module: str,
                                        _ = Depends(ABACPermissionCheck(Device.READ))):
    """The module's complete reported twin properties (read-only)."""
    return await _get_module_twin_reported(device, module)
