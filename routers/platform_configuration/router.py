from fastapi import Depends
from routers.base_api_router import BaseAPIRouter
from db.repos.device import DeviceRepository
from db.session import get_repository

from .schemas import (
    TemplateListResponse,
    SelectedTemplatesRequest,
    EndpointTypeUpdateRequest,
    ServiceUpdateRequest,
    DeviceTemplateConfigResponse,
    UpdateDeviceTemplateConfigRequest,
)

from .service import (get_available_templates)


platform_config = BaseAPIRouter(
    prefix="/platform",
    tags=["Platform Configuration"]
)


# ==================== TEMPLATES ====================

@platform_config.get("/devices/available-templates", response_model=TemplateListResponse)
async def get_available_templates_route(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    selected = await repo.get_selected_templates()
    templates = await get_available_templates(selected)
    return {"templates": templates}


@platform_config.post("/devices/selected-templates")
async def update_selected_templates(
    request: SelectedTemplatesRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    await repo.save_selected_templates(request.templates)
    return {"status": "updated"}


# ==================== ENDPOINT TYPES ====================

@platform_config.get("/device-endpoints/types")
async def get_endpoint_types(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    data = await repo.get_endpoint_types()
    return {"types": data}


@platform_config.post("/device-endpoints/types")
async def update_endpoint_types(
    request: EndpointTypeUpdateRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
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
):
    data = await repo.get_service_ports()
    return {"services": data}

@platform_config.post("/device-endpoints/services")
async def update_services(
    request: ServiceUpdateRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
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


# ==================== DEVICE TEMPLATE CONFIG ====================

@platform_config.get("/device-template-config", response_model=DeviceTemplateConfigResponse)
async def get_device_template_config(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    config = await repo.get_device_template_config()
    return DeviceTemplateConfigResponse(config=config)


@platform_config.patch("/device-template-config", response_model=DeviceTemplateConfigResponse)
async def update_device_template_config(
    request: UpdateDeviceTemplateConfigRequest,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    config = await repo.update_device_template_config(request.config)
    return DeviceTemplateConfigResponse(config=config)
