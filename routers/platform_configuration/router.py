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
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    selected = await repo.get_selected_templates()
    templates = await get_available_templates(selected)
    return {"templates": templates}


@platform_config.post("/devices/selected-templates")
async def update_selected_templates(
    request: SelectedTemplatesRequest,
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.save_selected_templates(request.templates)
    return {"status": "updated"}


# ==================== DEVICE TEMPLATE VARIABLES ====================

@platform_config.get("/device-template-variables", response_model=TemplateVariableListResponse)
async def get_template_variables(
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    variables = await repo.get_template_variables()
    return TemplateVariableListResponse(variables=variables)


@platform_config.put("/device-template-variables/{name}", response_model=TemplateVariableListResponse)
async def set_template_variable(
    name: str,
    request: SetTemplateVariableRequest,
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    variables = await repo.set_template_variable(name, request.value)
    return TemplateVariableListResponse(variables=variables)


@platform_config.delete("/device-template-variables/{name}")
async def delete_template_variable(
    name: str,
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.delete_template_variable(name)
    return {"status": "deleted"}
