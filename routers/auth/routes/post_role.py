from db.repos.role import RoleRepository
from routers.auth.schemas import RoleCreateRequest


async def post_role(body: RoleCreateRequest, role_repo: RoleRepository):
    return await role_repo.create_role(
        name=body.name,
        description=body.description,
        action_names=body.actions,
    )
