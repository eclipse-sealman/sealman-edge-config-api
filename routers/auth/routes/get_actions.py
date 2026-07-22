from db.repos.role import RoleRepository


async def get_actions(role_repo: RoleRepository):
    return await role_repo.list_actions()

