from routers.base_api_router import BaseAPIRouter

from .schemas import (
    TemplateListResponse,
    SelectedTemplatesRequest,
    EndpointTypeUpdateRequest,
    ServiceUpdateRequest,
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

@platform_config.get(
    "/devices/available-templates",
    response_model=TemplateListResponse
)
async def get_available_templates_route():

    templates = await get_available_templates()

    return {"templates": templates}


@platform_config.post("/devices/selected-templates")
async def update_selected_templates(
        request: SelectedTemplatesRequest):

    payload = {
        "selected": request.templates
    }

    await write_json_blob(
        PLATFORM_CONTAINER,
        TEMPLATES_FILE,
        payload
    )

    return {"status": "updated"}


# ==================== ENDPOINT TYPES ====================

@platform_config.get("/device-endpoints/types")
async def get_endpoint_types():

    data = await read_json_blob(
        PLATFORM_CONTAINER,
        ENDPOINT_TYPES_FILE
    )
    return {"types": data}


@platform_config.post("/device-endpoints/types")
async def update_endpoint_types(
        request: EndpointTypeUpdateRequest):

    payload = [
        {
            "name": item.name,
            "description": item.description,
            "defaultIP": item.defaultIP
        }
        for item in request.types
    ]

    await write_json_blob(
        PLATFORM_CONTAINER,
        ENDPOINT_TYPES_FILE,
        payload
    )

    return {"status": "updated"}


# ==================== SERVICES ====================

@platform_config.get("/device-endpoints/services")
async def get_services():

    data = await read_json_blob(
        PLATFORM_CONTAINER,
        SERVICES_FILE
    )
    return {"services": data}


@platform_config.post("/device-endpoints/services")
async def update_services(
        request: ServiceUpdateRequest):

    payload = [
        {
            "deviceEndpointServiceName": s.deviceEndpointServiceName,
            "description": s.description,
            "defaultPort": s.defaultPort
        }
        for s in request.services
    ]

    await write_json_blob(
        PLATFORM_CONTAINER,
        SERVICES_FILE,
        payload
    )

    return {"status": "updated"}
