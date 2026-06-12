import logging
from uuid import UUID

from fastapi import Depends, HTTPException, Query

from auth import validate_jwt
from authorization.abac_permission_check import ABACPermissionCheck, evaluate_permission
from authorization.permission_types import Device, Platform
from db.repos.action import ActionRepository
from db.repos.device import DeviceRepository
from db.repos.scope import ScopeRepository
from db.repos.team import TeamRepository
from db.repos.user import UserRepository
from exceptions import UserNotFound
from routers.auth.routes import action, role, scope, team
from db.repos.role import RoleRepository
from db.session import get_repository
from routers.auth.schemas import (
    ActionResponse,
    RoleActionsRequest,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
    ScopeCreateRequest,
    ScopeDetailsResponse,
    ScopeListResponse,
    ScopeResponse,
    ScopeUpdateRequest,
    TeamAddRoleRequest,
    TeamAddUserRequest,
    TeamCreateRequest,
    TeamDetailsResponse,
    TeamListResponse,
    TeamSummaryResponse,
    TeamUpdateRequest,
    UserListResponse,
    UserPermissions,
)
from routers.base_api_router import BaseAPIRouter


auth = BaseAPIRouter(
    prefix="/auth",
    tags=["Authorization"]
)

@auth.get("/roles", response_model=list[RoleResponse])
async def get_roles(_ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)), 
                    role_repo: RoleRepository = Depends(get_repository(RoleRepository))):
    return await role.get_roles(role_repo)


@auth.get("/actions", response_model=list[ActionResponse])
async def get_actions(_ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)),
                     action_repo: ActionRepository = Depends(get_repository(ActionRepository))):
    return await action.get_actions(action_repo)


@auth.post("/roles", response_model=RoleResponse)
async def post_role(
    body: RoleCreateRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
    action_repo: ActionRepository = Depends(get_repository(ActionRepository)),
):
    return await role.post_role(body, role_repo, action_repo)


@auth.put("/roles/{role_id}", response_model=RoleResponse)
async def put_role_by_id(
    role_id: UUID,
    body: RoleUpdateRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.put_role_by_id(role_id, body, role_repo)


@auth.post("/roles/{role_id}/actions", response_model=RoleResponse)
async def post_role_actions(
    role_id: UUID,
    body: RoleActionsRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
    action_repo: ActionRepository = Depends(get_repository(ActionRepository)),
):
    return await role.post_role_actions(role_id, body, role_repo, action_repo)


@auth.delete("/roles/{role_id}/actions/{name}", response_model=RoleResponse)
async def delete_role_action_by_name(
    role_id: UUID,
    name: str,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.delete_role_action_by_name(role_id, name, role_repo)


@auth.delete("/roles/{role_id}", response_model=None, status_code=204)
async def delete_role(
    role_id: UUID,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.delete_role(role_id, role_repo)


@auth.get("/teams", response_model=TeamListResponse)
async def get_teams(_ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)),
                   team_repo: TeamRepository = Depends(get_repository(TeamRepository))):
    return await team.get_teams(team_repo)


@auth.get("/teams/{team_id}", response_model=TeamDetailsResponse)
async def get_team_by_id(team_id: UUID,
                        _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)),
                        team_repo: TeamRepository = Depends(get_repository(TeamRepository))):
    return await team.get_team_by_id(team_id, team_repo)


@auth.post("/teams", response_model=TeamDetailsResponse)
async def create_team(
    request: TeamCreateRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await team.create_team(request, team_repo, scope_repo, user_repo, role_repo)


@auth.put("/teams/{team_id}", response_model=TeamSummaryResponse)
async def update_team(
    team_id: UUID,
    request: TeamUpdateRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await team.update_team(team_id, request, team_repo, scope_repo)


@auth.post("/teams/{team_id}/users", response_model=TeamDetailsResponse)
async def add_user_to_team(
    team_id: UUID,
    request: TeamAddUserRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
):
    return await team.add_user_to_team(team_id, request, team_repo, user_repo)


@auth.delete("/teams/{team_id}/users/{user_id}", response_model=None, status_code=204)
async def remove_user_from_team(
    team_id: UUID,
    user_id: str,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
):
    return await team.remove_user_from_team(team_id, user_id, team_repo)


@auth.post("/teams/{team_id}/roles", response_model=TeamDetailsResponse)
async def add_role_to_team(
    team_id: UUID,
    request: TeamAddRoleRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await team.add_role_to_team(team_id, request, team_repo, role_repo)


@auth.delete("/teams/{team_id}/roles/{role_id}", response_model=None, status_code=204)
async def remove_role_from_team(
    team_id: UUID,
    role_id: UUID,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
):
    return await team.remove_role_from_team(team_id, role_id, team_repo)


@auth.delete("/teams/{team_id}", response_model=None, status_code=204)
async def delete_team(
    team_id: UUID,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
):
    return await team.delete_team(team_id, team_repo)


@auth.get("/users", response_model=UserListResponse)
async def get_users(
    is_new: bool | None = Query(default=None),
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
):
    return await user_repo.list(is_new=is_new)


@auth.get("/scopes", response_model=ScopeListResponse)
async def get_scopes(_ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)),
                    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository))):
    return await scope.get_scopes(scope_repo)


@auth.get("/scopes/{scope_id}", response_model=ScopeDetailsResponse)
async def get_scope_details(
    scope_id: UUID,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_READ)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await scope.get_scope_details(scope_id, scope_repo)


@auth.post("/scopes", response_model=ScopeResponse)
async def create_scope(
    request: ScopeCreateRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await scope.create_scope(request, scope_repo)


@auth.put("/scopes/{scope_id}", response_model=ScopeResponse)
async def update_scope(
    scope_id: UUID,
    request: ScopeUpdateRequest,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await scope.update_scope(scope_id, request, scope_repo)


@auth.delete("/scopes/{scope_id}", response_model=None, status_code=204)
async def delete_scope(
    scope_id: UUID,
    _ = Depends(ABACPermissionCheck(Platform.AUTHORIZATION_WRITE)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await scope.delete_scope(scope_id, scope_repo)


@auth.get("/permissions/platform", response_model=UserPermissions)
async def get_platform_permissions(
    auth_context: dict = Depends(validate_jwt),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
):
    user_id = auth_context.get("oid") or auth_context.get("sub")
    if not user_id:
        raise HTTPException(status_code=403, detail="User identifier missing from token")

    user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
    if user_data is None:
        raise HTTPException(status_code=403, detail="User not found")

    permissions = [
        permission
        for permission in Platform.ReadPermissions + Platform.EditPermissions
        if evaluate_permission(permission, user_data)
    ]

    return UserPermissions(
        Permissions=permissions,
    )


@auth.get("/permissions/device", response_model=UserPermissions)
async def get_device_permissions(
    device_id: str,
    auth_context: dict = Depends(validate_jwt),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    user_id = auth_context.get("oid") or auth_context.get("sub")
    if not user_id:
        raise HTTPException(status_code=403, detail="User identifier missing from token")

    user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
    if user_data is None:
        raise HTTPException(status_code=403, detail="User not found")

    device_meta = await device_repo.get_device_meta_raw(device_id)
    if device_meta is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    permissions = [
        permission
        for permission in Device.ReadPermissions + Device.EditPermissions
        if evaluate_permission(permission, user_data, device_meta)
    ]

    return UserPermissions(
        Permissions=permissions,
    )

