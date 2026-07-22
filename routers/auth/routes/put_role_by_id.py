from uuid import UUID

from db.repos.role import RoleRepository
from routers.auth.schemas import RoleUpdateRequest


async def put_role_by_id(role_id: UUID, body: RoleUpdateRequest, role_repo: RoleRepository):
    return await role_repo.update_role(
        role_id=role_id,
        name=body.name,
        description=body.description,
        action_names=body.actions,
    )
