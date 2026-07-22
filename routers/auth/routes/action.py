from db.repos.action import ActionRepository


async def get_actions(action_repo: ActionRepository):
    return await action_repo.list()
