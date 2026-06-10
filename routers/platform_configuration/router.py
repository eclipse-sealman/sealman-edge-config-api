from fastapi import Depends
from routers.base_api_router import BaseAPIRouter
from db.repos.device import DeviceRepository
from db.session import get_repository

from .schemas import (
    TemplateListResponse,
    SelectedTemplatesRequest,
    EndpointTypeUpdateRequest,
    ServiceUpdateRequest,
    AddMetadataKeyRequest,
    MetadataKeysResponse,
    MetadataKeyOptions,
)

from .service import (
    read_json_blob,
    write_json_blob,
    get_available_templates,
    PLATFORM_CONTAINER,
    TEMPLATES_FILE,
    ENDPOINT_TYPES_FILE,
    SERVICES_FILE,
)


platform_config = BaseAPIRouter(
    prefix="/platform",
    tags=["Platform Configuration"]
)


# ==================== TEMPLATES ====================

@platform_config.get("/devices/available-templates", response_model=TemplateListResponse)
async def get_available_templates_route():
    templates = await get_available_templates()
    return {"templates": templates}


@platform_config.post("/devices/selected-templates")
async def update_selected_templates(request: SelectedTemplatesRequest):
    payload = {"selected": request.templates}
    await write_json_blob(PLATFORM_CONTAINER, TEMPLATES_FILE, payload)
    return {"status": "updated"}


# ==================== ENDPOINT TYPES ====================

@platform_config.get("/device-endpoints/types")
async def get_endpoint_types():
    data = await read_json_blob(PLATFORM_CONTAINER, ENDPOINT_TYPES_FILE)
    return {"types": data}


@platform_config.post("/device-endpoints/types")
async def update_endpoint_types(request: EndpointTypeUpdateRequest):
    payload = [
        {"name": item.name, "description": item.description, "defaultIP": item.defaultIP}
        for item in request.types
    ]
    await write_json_blob(PLATFORM_CONTAINER, ENDPOINT_TYPES_FILE, payload)
    return {"status": "updated"}


# ==================== SERVICES ====================

@platform_config.get("/device-endpoints/services")
async def get_services():
    data = await read_json_blob(PLATFORM_CONTAINER, SERVICES_FILE)
    return {"services": data}


@platform_config.post("/device-endpoints/services")
async def update_services(request: ServiceUpdateRequest):
    payload = [
        {
            "deviceEndpointServiceName": s.deviceEndpointServiceName,
            "description": s.description,
            "defaultPort": s.defaultPort,
        }
        for s in request.services
    ]
    await write_json_blob(PLATFORM_CONTAINER, SERVICES_FILE, payload)
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
):
    meta = await repo.get_platform_meta_keys()
    return MetadataKeysResponse(keys=_meta_to_response_keys(meta))


@platform_config.post("/metadata/keys", response_model=MetadataKeysResponse, status_code=201)
async def add_metadata_key(
    request: AddMetadataKeyRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    key_name, key_options = next(iter(request.key.items()))
    meta = await repo.add_platform_meta_key(key_name, key_options.model_dump())
    return MetadataKeysResponse(keys=_meta_to_response_keys(meta))


@platform_config.delete("/metadata/keys/{key}", response_model=MetadataKeysResponse)
async def delete_metadata_key(
    key: str,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    meta = await repo.delete_platform_meta_key(key)
    return MetadataKeysResponse(keys=_meta_to_response_keys(meta))
