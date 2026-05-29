from uuid import UUID

from fastapi import Depends, HTTPException, Query

from auth import RBACPermissionChecker, validate_jwt
from authorization.permission_types import Device, Platform
from constants import AUTHORIZATION_API_PLATFORM_NAME
from db.repos.action import ActionRepository
from db.repos.scope import ScopeRepository
from db.repos.team import TeamRepository
from db.repos.user_context import UserContextRepository
from db.session import get_repository
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


auth = BaseAPIRouter()


@auth.get("/auth/roles", response_model=list[RoleResponse], tags=["Auth"])
async def get_roles(role_repo: RoleRepository = Depends(get_repository(RoleRepository))):
    return await role.get_roles(role_repo)


@auth.get("/auth/actions", response_model=list[ActionResponse], tags=["Auth"])
async def get_actions(action_repo: ActionRepository = Depends(get_repository(ActionRepository))):
    return await action.get_actions(action_repo)


@auth.post("/auth/roles", response_model=RoleResponse, tags=["Auth"])
async def post_role(
    body: RoleCreateRequest,
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.post_role(body, role_repo)


@auth.put("/auth/roles/{role_id}", response_model=RoleResponse, tags=["Auth"])
async def put_role_by_id(
    role_id: UUID,
    body: RoleUpdateRequest,
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.put_role_by_id(role_id, body, role_repo)


@auth.post("/auth/roles/{role_id}/actions", response_model=RoleResponse, tags=["Auth"])
async def post_role_actions(
    role_id: UUID,
    body: RoleActionsRequest,
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.post_role_actions(role_id, body, role_repo)


@auth.delete(
    "/auth/roles/{role_id}/actions/{name}",
    response_model=RoleResponse,
    tags=["Auth"],
)
async def delete_role_action_by_name(
    role_id: UUID,
    name: str,
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await role.delete_role_action_by_name(role_id, name, role_repo)


@auth.get(
    "/auth/permissions/{resource_type}", response_model=UserPermissions, tags=["Auth"]
)
async def get_permissions(
    resource_type: str,
    resource_id: str | None = None,
    auth_context: dict = Depends(validate_jwt),
):

    if resource_type == "platform" and resource_id is None:
        resource_id = AUTHORIZATION_API_PLATFORM_NAME

    if resource_id is None:
        raise HTTPException(status_code=400, detail="resource_id is required")

    user_id = auth_context.get("oid") or auth_context.get("sub")
    if user_id is None:
        raise UserNotFound(status_code=403)

    # In RBAC mode, derive permissions from the user's assigned roles.
    if resource_type.lower() == "platform":
        permission_resource_type = Platform
    elif resource_type.lower() == "device":
        permission_resource_type = Device
    else:
        raise HTTPException(status_code=400, detail="Invalid resource type")

    assigned_permissions = RBACPermissionChecker.get_assigned_permissions(auth_context)
    assigned_permission_map = {perm: True for perm in assigned_permissions}
    available_permissions = {
        value
        for attr in dir(permission_resource_type)
        if not attr.startswith("__")
        for value in [getattr(permission_resource_type, attr)]
        if isinstance(value, str)
    }
    user_permissions = [
        permission for permission in available_permissions if permission in assigned_permission_map
    ]

    return UserPermissions(
        ResourceType=resource_type,
        ResourceId=resource_id,
        Permissions=user_permissions,
    )


@auth.get("/teams", response_model=TeamListResponse, tags=["Auth"])
async def get_teams(team_repo: TeamRepository = Depends(get_repository(TeamRepository))):
    return await team.get_teams(team_repo)


@auth.get("/teams/{team_id}", response_model=TeamDetailsResponse, tags=["Auth"])
async def get_team_by_id(team_id: UUID, team_repo: TeamRepository = Depends(get_repository(TeamRepository))):
    return await team.get_team_by_id(team_id, team_repo)


@auth.post("/teams", response_model=TeamSummaryResponse, tags=["Auth"])
async def create_team(
    request: TeamCreateRequest,
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await team.create_team(request, team_repo, scope_repo)


@auth.put("/teams/{team_id}", response_model=TeamSummaryResponse, tags=["Auth"])
async def update_team(
    team_id: UUID,
    request: TeamUpdateRequest,
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await team.update_team(team_id, request, team_repo, scope_repo)


@auth.post("/teams/{team_id}/users", response_model=TeamDetailsResponse, tags=["Auth"])
async def add_user_to_team(
    team_id: UUID,
    request: TeamAddUserRequest,
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    user_repo: UserContextRepository = Depends(get_repository(UserContextRepository)),
):
    return await team.add_user_to_team(team_id, request, team_repo, user_repo)


@auth.delete("/teams/{team_id}/users/{user_id}", response_model=None, status_code=204, tags=["Auth"])
async def remove_user_from_team(
    team_id: UUID,
    user_id: str,
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
):
    return await team.remove_user_from_team(team_id, user_id, team_repo)


@auth.post("/teams/{team_id}/roles", response_model=TeamDetailsResponse, tags=["Auth"])
async def add_role_to_team(
    team_id: UUID,
    request: TeamAddRoleRequest,
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
    role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
):
    return await team.add_role_to_team(team_id, request, team_repo, role_repo)


@auth.delete("/teams/{team_id}/roles/{role_id}", response_model=None, status_code=204, tags=["Auth"])
async def remove_role_from_team(
    team_id: UUID,
    role_id: UUID,
    team_repo: TeamRepository = Depends(get_repository(TeamRepository)),
):
    return await team.remove_role_from_team(team_id, role_id, team_repo)


@auth.get("/users", response_model=UserListResponse, tags=["Auth"])
async def get_users(
    is_new_user: bool | None = Query(default=None),
    user_repo: UserContextRepository = Depends(get_repository(UserContextRepository)),
):
    return await user_repo.list(is_new_user=is_new_user)


@auth.get("/scopes", response_model=ScopeListResponse, tags=["Auth"])
async def get_scopes(scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository))):
    return await scope.get_scopes(scope_repo)


@auth.post("/scopes", response_model=ScopeResponse, tags=["Auth"])
async def create_scope(
    request: ScopeCreateRequest,
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await scope.create_scope(request, scope_repo)


@auth.put("/scopes/{scope_id}", response_model=ScopeResponse, tags=["Auth"])
async def update_scope(
    scope_id: UUID,
    request: ScopeUpdateRequest,
    scope_repo: ScopeRepository = Depends(get_repository(ScopeRepository)),
):
    return await scope.update_scope(scope_id, request, scope_repo)
