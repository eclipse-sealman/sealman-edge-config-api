from typing import cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from db.repos.action import ActionRepository
from db.repos.role import RoleRepository
from exceptions import APIError
from routers.auth.schemas import (
    RoleCreateRequest,
    RoleUpdateRequest,
)


async def get_roles(role_repo: RoleRepository):
    return await role_repo.list_roles()


async def get_role_by_id(role_id: UUID, role_repo: RoleRepository):
    role = await role_repo.get(role_id)
    if role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)

    teams = await role_repo.list_teams(role_id)
    return {
        **role,
        "teams": teams,
    }


async def post_role(
    body: RoleCreateRequest,
    role_repo: RoleRepository,
    action_repo: ActionRepository,
):
    if len(body.actions) != len(set(body.actions)):
        raise APIError("Duplicate action names in request", 400)

    if body.actions:
        found_actions = await action_repo.get_by_names(body.actions)
        found_action_names = {action["name"] for action in found_actions}
        missing_actions = sorted(set(body.actions) - found_action_names)
        if missing_actions:
            raise APIError(f"Actions not found: {', '.join(missing_actions)}", 404)

    try:
        return await role_repo.create_role(
            name=body.name,
            description=body.description,
            action_names=body.actions,
        )
    except IntegrityError:
        raise APIError(f"Role with name '{body.name}' already exists", 409)


async def put_role_by_id(
    role_id: UUID,
    body: RoleUpdateRequest,
    role_repo: RoleRepository,
    action_repo: ActionRepository,
):
    payload = body.model_dump()
    role_name = cast(str, payload["name"])
    description = cast(str | None, payload["description"])
    actions = cast(list[str], payload["actions"])

    if await role_repo.get(role_id) is None:
        raise APIError(f"Role '{role_id}' was not found", 404)

    existing_role = await role_repo.get_by_name(role_name)
    if existing_role is not None and cast(UUID, existing_role["id"]) != role_id:
        raise APIError(f"Role name '{role_name}' already exists", 409)

    if len(actions) != len(set(actions)):
        raise APIError("Duplicate action names in request", 400)

    found_actions = await action_repo.get_by_names(actions)
    found_action_names = {action["name"] for action in found_actions}
    missing_actions = sorted(set(actions) - found_action_names)
    if missing_actions:
        raise APIError(f"Actions not found: {', '.join(missing_actions)}", 404)

    updated_role = await role_repo.update_role(
        role_id=role_id,
        name=role_name,
        description=description,
        action_names=actions,
    )
    if updated_role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)
    return updated_role


async def delete_role(role_id: UUID, role_repo: RoleRepository):
    if await role_repo.get(role_id) is None:
        raise APIError(f"Role '{role_id}' was not found", 404)
    await role_repo.delete_role(role_id)
