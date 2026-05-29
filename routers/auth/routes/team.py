from typing import cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from db.repos.role import RoleRepository
from db.repos.scope import ScopeRepository
from db.repos.team import TeamRepository
from db.repos.user_context import UserContextRepository
from exceptions import APIError
from routers.auth.schemas import TeamAddRoleRequest, TeamAddUserRequest, TeamCreateRequest, TeamUpdateRequest


async def get_teams(team_repo: TeamRepository):
    return await team_repo.list()


async def get_team_by_id(team_id: UUID, team_repo: TeamRepository):
    team = await team_repo.get_with_details(team_id)
    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)
    return team


async def create_team(
    request: TeamCreateRequest,
    team_repo: TeamRepository,
    scope_repo: ScopeRepository,
):
    if request.scope_id is not None:
        scope = await scope_repo.get(request.scope_id)
        if scope is None:
            raise APIError(f"Scope '{request.scope_id}' was not found", 404)

    try:
        return await team_repo.create(name=request.name, scope_id=request.scope_id)
    except IntegrityError:
        raise APIError(f"Team with name '{request.name}' already exists", 409)


async def update_team(
    team_id: UUID,
    request: TeamUpdateRequest,
    team_repo: TeamRepository,
    scope_repo: ScopeRepository,
):
    if await team_repo.get(team_id) is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    if request.scope_id is not None:
        scope = await scope_repo.get(request.scope_id)
        if scope is None:
            raise APIError(f"Scope '{request.scope_id}' was not found", 404)

    try:
        team = await team_repo.update(
            team_id=team_id,
            name=request.name,
            scope_id=request.scope_id,
        )
    except IntegrityError:
        raise APIError(f"Team with name '{request.name}' already exists", 409)

    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    return team


async def add_user_to_team(
    team_id: UUID,
    request: TeamAddUserRequest,
    team_repo: TeamRepository,
    user_repo: UserContextRepository,
):
    team = await team_repo.get_with_details(team_id)
    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    if await user_repo.get(request.user_id) is None:
        raise APIError(f"User '{request.user_id}' was not found", 404)

    if any(cast(str, user["id"]) == request.user_id for user in cast(list[dict], team["users"])):
        raise APIError(f"User '{request.user_id}' already belongs to team '{team_id}'", 409)

    updated = await team_repo.add_user(team_id=team_id, user_id=request.user_id)
    if updated is None:
        raise APIError(f"Team '{team_id}' was not found", 404)
    return updated


async def remove_user_from_team(
    team_id: UUID,
    user_id: str,
    team_repo: TeamRepository,
):
    team = await team_repo.get_with_details(team_id)
    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    target_user = next((user for user in cast(list[dict], team["users"]) if cast(str, user["id"]) == user_id), None)
    if target_user is None:
        raise APIError(f"User '{user_id}' is not assigned to team '{team_id}'", 404)

    removed = await team_repo.remove_user(team_id, user_id)
    if not removed:
        raise APIError(f"User '{user_id}' is not assigned to team '{team_id}'", 404)


async def add_role_to_team(
    team_id: UUID,
    request: TeamAddRoleRequest,
    team_repo: TeamRepository,
    role_repo: RoleRepository,
):
    team = await team_repo.get_with_details(team_id)
    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    if any(cast(UUID, role["id"]) == request.role_id for role in cast(list[dict], team["roles"])):
        raise APIError(f"Role '{request.role_id}' already assigned to team '{team_id}'", 409)

    role = await role_repo.get(request.role_id)
    if role is None:
        raise APIError(f"Role '{request.role_id}' was not found", 404)

    updated = await team_repo.add_role(team_id, request.role_id)
    if updated is None:
        raise APIError(f"Team '{team_id}' was not found", 404)
    return updated


async def remove_role_from_team(
    team_id: UUID,
    role_id: UUID,
    team_repo: TeamRepository,
):
    team = await team_repo.get_with_details(team_id)
    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    target_role = next((role for role in cast(list[dict], team["roles"]) if cast(UUID, role["id"]) == role_id), None)
    if target_role is None:
        raise APIError(f"Role '{role_id}' is not assigned to team '{team_id}'", 404)

    removed = await team_repo.remove_role(team_id, role_id)
    if not removed:
        raise APIError(f"Role '{role_id}' is not assigned to team '{team_id}'", 404)


async def delete_team(team_id: UUID, team_repo: TeamRepository):
    team = await team_repo.get_with_details(team_id)
    if team is None:
        raise APIError(f"Team '{team_id}' was not found", 404)

    assigned_users = cast(list[dict], team["users"])
    if assigned_users:
        raise APIError(
            f"Team has {len(assigned_users)} assigned user(s) and cannot be deleted",
            409,
        )

    await team_repo.delete(team_id)

