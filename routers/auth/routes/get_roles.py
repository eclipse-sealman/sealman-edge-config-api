from db.repos.role import RoleRepository


async def get_roles(role_repo: RoleRepository):
    return await role_repo.list_roles()
