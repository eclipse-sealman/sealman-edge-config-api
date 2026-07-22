from uuid import UUID

from db.repos.role import RoleRepository
from routers.auth.schemas import RoleActionsRequest


async def post_role_actions(role_id: UUID, body: RoleActionsRequest, role_repo: RoleRepository):
    return await role_repo.add_actions_to_role(role_id=role_id, action_names=body.names)
