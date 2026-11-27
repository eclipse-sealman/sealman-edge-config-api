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
    TestDeviceConfigResponse,
    IoTHubInfo,
    DeviceCreationReadiness,
    BaseDeploymentUpdate,
    BaseDeploymentResponse,
)

from .service import (get_available_templates, test_device_config, validate_template_variable)
from .readiness import missing_required_variables
from .base_deployment import get_base_deployment, save_base_deployment, rerender_stored_base_deployment
from bootstrap.base_deployment_manifest import MANIFEST_VARIABLES
from db.repos.base_deployment import BaseDeploymentRepository

from authorization.abac_permission_check import ABACAnyPermissionCheck, ABACPermissionCheck
from authorization.permission_types import Device, Platform
from constants import AZURE_IOT_EDGE_ENABLED_VARIABLE, IOT_HUB_NAME


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
    base_deployment_repo: BaseDeploymentRepository = Depends(get_repository(BaseDeploymentRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    validate_template_variable(name, request.value)
    variables = await repo.set_template_variable(name, request.value)
    if name in MANIFEST_VARIABLES:
        await rerender_stored_base_deployment(repo, base_deployment_repo)
    if request.value == "":
        # SEMS rejects blank variable values; the twig default applies once the variable is gone.
        await SmartEMS.delete_variable_for_all_devices(name=name)
    else:
        await SmartEMS.set_variable_for_all_devices(name=name, value=request.value)
    return TemplateVariableListResponse(variables=variables)


@platform_config.delete("/device-template-variables/{name}")
async def delete_template_variable(
    name: str,
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    base_deployment_repo: BaseDeploymentRepository = Depends(get_repository(BaseDeploymentRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.delete_template_variable(name)
    if name in MANIFEST_VARIABLES:
        await rerender_stored_base_deployment(repo, base_deployment_repo)
    await SmartEMS.delete_variable_for_all_devices(name=name)
    return {"status": "deleted"}


# Readable by device creators as well, so the create dialog can explain why creation is blocked.
@platform_config.get("/device-creation-readiness", response_model=DeviceCreationReadiness)
async def get_device_creation_readiness(
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    functions_repo: PlatformFunctionsRepository = Depends(get_repository(PlatformFunctionsRepository)),
    _ = Depends(ABACAnyPermissionCheck(Platform.CONFIG_READ, Device.CREATE)),
):
    missing = missing_required_variables(
        await repo.get_template_variables(),
        (await functions_repo.get_functions()).azure_iot_edge_enabled,
    )
    return DeviceCreationReadiness(ready=not missing, missing=missing)


@platform_config.post("/test-device-config", response_model=TestDeviceConfigResponse)
async def test_device_config_route(
    repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    functions_repo: PlatformFunctionsRepository = Depends(get_repository(PlatformFunctionsRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    return await test_device_config(
        await repo.get_template_variables(),
        (await functions_repo.get_functions()).azure_iot_edge_enabled,
    )


# ==================== BASE DEPLOYMENT ====================

@platform_config.get("/base-deployment", response_model=BaseDeploymentResponse, response_model_exclude_none=False)
async def get_base_deployment_route(
    template_repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    repo: BaseDeploymentRepository = Depends(get_repository(BaseDeploymentRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    return await get_base_deployment(template_repo, repo)


@platform_config.put("/base-deployment", response_model=BaseDeploymentResponse, response_model_exclude_none=False)
async def save_base_deployment_route(
    request: BaseDeploymentUpdate,
    template_repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    repo: BaseDeploymentRepository = Depends(get_repository(BaseDeploymentRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    return await save_base_deployment(
        [m.model_dump() for m in request.modules], request.edge_hub_env, template_repo, repo
    )


@platform_config.delete("/base-deployment", response_model=BaseDeploymentResponse, response_model_exclude_none=False)
async def reset_base_deployment_route(
    template_repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    repo: BaseDeploymentRepository = Depends(get_repository(BaseDeploymentRepository)),
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.delete()
    return await get_base_deployment(template_repo, repo)


# ==================== IOT HUB ====================

@platform_config.get("/iot-hub", response_model=IoTHubInfo)
async def get_iot_hub(
    _ = Depends(ABACPermissionCheck(Platform.CONFIG_READ, device_path=None)),
):
    return IoTHubInfo(hostname=IOT_HUB_NAME or None)


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
    new_settings = PlatformFunctionSettings(**request.model_dump())
    if new_settings.azure_iot_edge_enabled != (await repo.get_functions()).azure_iot_edge_enabled:
        # Pushed before saving so a SEMS failure leaves the old state and the change can be retried.
        await SmartEMS.set_variable_for_all_devices(
            name=AZURE_IOT_EDGE_ENABLED_VARIABLE,
            value="true" if new_settings.azure_iot_edge_enabled else "false",
        )
    return asdict(await repo.save_functions(new_settings))
