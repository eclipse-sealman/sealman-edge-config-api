from typing import cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from db.repos.action import ActionRepository
from db.repos.role import RoleRepository
from exceptions import APIError
from routers.auth.schemas import RoleActionsRequest, RoleCreateRequest, RoleUpdateRequest


async def get_roles(role_repo: RoleRepository):
    return await role_repo.list_roles()


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


async def put_role_by_id(role_id: UUID, body: RoleUpdateRequest, role_repo: RoleRepository):
    if await role_repo.get(role_id) is None:
        raise APIError(f"Role '{role_id}' was not found", 404)

    existing_role = await role_repo.get_by_name(body.name)
    if existing_role is not None and cast(UUID, existing_role["id"]) != role_id:
        raise APIError(f"Role name '{body.name}' already exists", 409)

    updated_role = await role_repo.update_role(
        role_id=role_id,
        name=body.name,
        description=body.description,
    )
    if updated_role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)
    return updated_role


async def post_role_actions(
    role_id: UUID,
    body: RoleActionsRequest,
    role_repo: RoleRepository,
    action_repo: ActionRepository,
):
    role = await role_repo.get(role_id)
    if role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)

    if len(body.names) != len(set(body.names)):
        raise APIError("Duplicate action names in request", 400)

    found_actions = await action_repo.get_by_names(body.names)
    found_action_names = {action["name"] for action in found_actions}
    missing_actions = sorted(set(body.names) - found_action_names)
    if missing_actions:
        raise APIError(f"Actions not found: {', '.join(missing_actions)}", 404)

    conflicting_actions = sorted(set(body.names) & set(cast(list[str], role["actions"])))
    if conflicting_actions:
        raise APIError(
            f"Actions already assigned to role: {', '.join(conflicting_actions)}",
            409,
        )

    updated_role = await role_repo.add_actions_to_role(role_id=role_id, action_names=body.names)
    if updated_role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)
    return updated_role


async def delete_role_action_by_name(role_id: UUID, name: str, role_repo: RoleRepository):
    role = await role_repo.get(role_id)
    if role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)

    if name not in cast(list[str], role["actions"]):
        raise APIError(f"Action '{name}' is not assigned to role '{role_id}'", 404)

    updated_role = await role_repo.remove_action_from_role(role_id=role_id, action_name=name)
    if updated_role is None:
        raise APIError(f"Role '{role_id}' was not found", 404)
    return updated_role


async def delete_role(role_id: UUID, role_repo: RoleRepository):
    if await role_repo.get(role_id) is None:
        raise APIError(f"Role '{role_id}' was not found", 404)
    await role_repo.delete_role(role_id)
