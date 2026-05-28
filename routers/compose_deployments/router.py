import json

from fastapi import Depends, HTTPException, Query
from constants import AUTHORIZATION_API_PLATFORM_NAME
from authorization.permission_check import PermissionCheck
from authorization import resource_types as Resource
from authorization.permission_types import Platform
from db.repos.compose import ComposeRepository
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter
from smart_ems import SmartEMS
from .schemas import (
    ActiveDeploymentResponse,
    ComposeRequest,
    ComposeResponse,
    DeploymentListItem,
    DeploymentResponseUnion,
    MessageResponse,
)
from .lp_compose_builder import LPComposeBuilder


def _platform_read():
    return Depends(
        PermissionCheck(
            Platform.READ_DEPLOYMENT_LIST,
            Resource.PLATFORM,
            AUTHORIZATION_API_PLATFORM_NAME,
        )
    )


compose_deployment = BaseAPIRouter(
    prefix="/compose-deployments",
    tags=["Compose Deployments"],
)


@compose_deployment.get("", response_model=list[DeploymentListItem])
async def list_deployments(
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    return await repository.list()


@compose_deployment.get("/{name}", response_model=DeploymentResponseUnion)
async def get_deployment(
    name: str,
    filter: str | None = Query(None, description="sems | compose | request"),
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    deployment = await repository.get(name)

    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if filter == "sems":
        return deployment["sems_compose"]
    elif filter == "compose":
        return deployment["compose"]
    elif filter == "request":
        return deployment["request"]

    return deployment


@compose_deployment.put("/{name}", response_model=ComposeResponse)
async def create_or_update_deployment(
    name: str,
    req: ComposeRequest,
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    compose = LPComposeBuilder.build_compose(name, req.services)

    created = await repository.create_or_update(
        name=name,
        request=req.model_dump(),
        content=compose,
        description=req.description,
        landing_page=False,
    )

    if not created:
        raise HTTPException(
            status_code=409,
            detail=f"Deployment '{name}' already exists",
        )

    return ComposeResponse(name=name, description=req.description, compose=compose)


@compose_deployment.delete("/{name}", response_model=MessageResponse)
async def delete_deployment(
    name: str,
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    deleted = await repository.delete(name)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Deployment '{name}' not found",
        )

    return {"message": f"{name} deleted successfully"}


active_deployment = BaseAPIRouter(
    prefix="/active-deployment",
    tags=["Compose Deployments"],
)


@active_deployment.get("", response_model=ActiveDeploymentResponse)
async def get_active_deployment(
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    active = await repository.get_active_deployment()
    return {"active_deployment": active or None}


@active_deployment.put("/{name}", response_model=ActiveDeploymentResponse)
async def set_active_deployment(
    name: str,
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    deployment = await repository.get(name)

    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    sems_compose = deployment.get("sems_compose")
    if not sems_compose:
        raise HTTPException(
            status_code=422,
            detail="Deployment has no SEMS compose",
        )

    await SmartEMS.set_variable_for_all_devices(
        name="compose_deployment",
        value=json.dumps(sems_compose),
    )
    await repository.set_active_deployment(name)

    return {"active_deployment": name, "status": "applied to all devices"}


@active_deployment.delete("", response_model=MessageResponse)
async def delete_active_deployment(
    repository: ComposeRepository = Depends(get_repository(ComposeRepository)),
    _=_platform_read(),
):
    deleted = await repository.delete_active_deployment()

    if not deleted:
        raise HTTPException(status_code=404, detail="No active deployment found")

    await SmartEMS.set_variable_for_all_devices(
        name="compose_deployment",
        value=json.dumps({}),
    )

    return {"message": "Active deployment cleared"}
