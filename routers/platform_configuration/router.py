from fastapi import Depends
from routers.base_api_router import BaseAPIRouter
from db.repos.device_template import DeviceTemplateRepository
from db.session import get_repository

from .schemas import (
    TemplateListResponse,
    SelectedTemplatesRequest,
    TemplateVariableListResponse,
    SetTemplateVariableRequest,
)

from .service import (get_available_templates)

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Platform


platform_config = BaseAPIRouter(
    prefix="/platform",
    tags=["Platform Configuration"]
)


# ==================== TEMPLATES ====================

@platform_config.get("/devices/available-templates", response_model=TemplateListResponse)
async def get_available_templates_route(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    selected = await repo.get_selected_templates()
    templates = await get_available_templates(selected)
    return {"templates": templates}


@platform_config.post("/devices/selected-templates")
async def update_selected_templates(
    request: SelectedTemplatesRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.save_selected_templates(request.templates)
    return {"status": "updated"}


# ==================== DEVICE TEMPLATE VARIABLES ====================

@platform_config.get("/device-endpoints/types")
async def get_endpoint_types(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    variables = await repo.get_template_variables()
    return TemplateVariableListResponse(variables=variables)


@platform_config.post("/device-endpoints/types")
async def update_endpoint_types(
    request: EndpointTypeUpdateRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    payload = [
        {"name": item.name, "description": item.description, "defaultIP": item.defaultIP}
        for item in request.types
    ]
    await repo.save_endpoint_types(payload)
    return {"status": "updated"}


# ==================== SERVICES ====================

@platform_config.get("/device-endpoints/services")
async def get_services(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    data = await repo.get_service_ports()
    return {"services": data}

@platform_config.post("/device-endpoints/services")
async def update_services(
    request: ServiceUpdateRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    payload = [
        {
            "deviceEndpointServiceName": s.deviceEndpointServiceName,
            "description": s.description,
            "defaultPort": s.defaultPort,
        }
        for s in request.services
    ]
    await repo.save_service_ports(payload)
    return {"status": "updated"}


# ==================== METADATA KEYS ====================

DEFAULT_OPTIONS = MetadataKeyOptions(prepopulate=False, allowAddition=False)


def _meta_to_response_keys(meta: dict) -> list:
    """
    Convert platform_meta dict to the response list format.
    Handles legacy values (None) by defaulting to {prepopulate: false, allowAddition: false}.
    """
    result = []
    for k, v in meta.items():
        if isinstance(v, dict):
            options = MetadataKeyOptions(
                prepopulate=v.get("prepopulate", False),
                allowAddition=v.get("allowAddition", False),
            )
        else:
            options = DEFAULT_OPTIONS
        result.append({k: options})
    return result


@platform_config.get("/metadata/keys", response_model=MetadataKeysResponse)
async def get_metadata_keys(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    meta = await repo.get_platform_meta_keys()
    return MetadataKeysResponse(keys=_meta_to_response_keys(meta))


@platform_config.post("/metadata/keys", response_model=MetadataKeysResponse, status_code=201)
async def add_metadata_key(
    request: AddMetadataKeyRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    key_name, key_options = next(iter(request.key.items()))
    meta = await repo.add_platform_meta_key(key_name, key_options.model_dump())
    return MetadataKeysResponse(keys=_meta_to_response_keys(meta))


@platform_config.delete("/metadata/keys/{key}", response_model=MetadataKeysResponse)
async def delete_metadata_key(
    key: str,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    meta = await repo.delete_platform_meta_key(key)
    return MetadataKeysResponse(keys=_meta_to_response_keys(meta))


# ==================== DEVICE TEMPLATE CONFIG ====================

@platform_config.get("/device-template-config", response_model=DeviceTemplateConfigResponse)
async def get_device_template_config(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    config = await repo.get_device_template_config()
    return DeviceTemplateConfigResponse(config=config)


@platform_config.patch("/device-template-config", response_model=DeviceTemplateConfigResponse)
async def update_device_template_config(
    request: UpdateDeviceTemplateConfigRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.delete_template_variable(name)
    return {"status": "deleted"}
