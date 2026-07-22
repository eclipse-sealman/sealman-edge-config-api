from uuid import UUID

from db.repos.role import RoleRepository


async def delete_role_action_by_name(role_id: UUID, name: str, role_repo: RoleRepository):
    return await role_repo.remove_action_from_role(role_id=role_id, action_name=name)
