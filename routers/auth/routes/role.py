from uuid import UUID

from db.repos.role import RoleRepository
from routers.auth.schemas import RoleActionsRequest, RoleCreateRequest, RoleUpdateRequest


async def get_roles(role_repo: RoleRepository):
    return await role_repo.list_roles()


async def post_role(body: RoleCreateRequest, role_repo: RoleRepository):
    return await role_repo.create_role(
        name=body.name,
        description=body.description,
        action_names=body.actions,
    )


async def put_role_by_id(role_id: UUID, body: RoleUpdateRequest, role_repo: RoleRepository):
    return await role_repo.update_role(
        role_id=role_id,
        name=body.name,
        description=body.description,
    )


async def post_role_actions(role_id: UUID, body: RoleActionsRequest, role_repo: RoleRepository):
    return await role_repo.add_actions_to_role(role_id=role_id, action_names=body.names)


async def delete_role_action_by_name(role_id: UUID, name: str, role_repo: RoleRepository):
    return await role_repo.remove_action_from_role(role_id=role_id, action_name=name)