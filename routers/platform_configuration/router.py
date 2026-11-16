from dataclasses import asdict

from fastapi import Depends
from routers.base_api_router import BaseAPIRouter
from db.repos.device_template import DeviceTemplateRepository
from db.repos.platform_functions import PlatformFunctionSettings, PlatformFunctionsRepository
from db.session import get_repository
from smart_ems import SmartEMS

from .schemas import (
    PlatformFunctions,
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
    await SmartEMS.set_variable_for_all_devices(name=name, value=request.value)
    return TemplateVariableListResponse(variables=variables)


@platform_config.delete("/device-template-variables/{name}")
async def delete_template_variable(
    name: str,
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.delete_template_variable(name)
    await SmartEMS.delete_variable_for_all_devices(name=name)
    return {"status": "deleted"}


# ==================== PLATFORM FUNCTIONS ====================

# Readable by every signed-in user: the UI needs it to decide which functions to offer at all.
@platform_config.get("/functions", response_model=PlatformFunctions)
async def get_platform_functions(
    repo: PlatformFunctionsRepository = Depends(get_repository(PlatformFunctionsRepository)),
):
    return asdict(await repo.get_functions())


@platform_config.put("/functions", response_model=PlatformFunctions)
async def update_platform_functions(
    request: PlatformFunctions,
    repo: PlatformFunctionsRepository = Depends(get_repository(PlatformFunctionsRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    return asdict(await repo.save_functions(PlatformFunctionSettings(**request.model_dump())))
