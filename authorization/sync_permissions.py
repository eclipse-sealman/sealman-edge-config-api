import logging

from sqlalchemy import select
from db.models.action import Action
from authorization.permission_types import get_all_permissions

logger = logging.getLogger("EdgeConfigAPI")


async def sync_permissions_to_db():
    """Ensures all permissions defined in permission_types exist in the database.
    Inserts any missing permissions on startup."""
    from db.session import AsyncSessionLocal

    all_permissions = get_all_permissions()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Action.name))
        existing_names = {row[0] for row in result.all()}

        new_actions = []
        for perm in all_permissions:
            if perm["name"] not in existing_names:
                new_actions.append(Action(
                    name=perm["name"],
                    description=perm["description"],
                    is_global=perm["is_global"],
                ))

        if new_actions:
            session.add_all(new_actions)
            await session.commit()
            for action in new_actions:
                logger.info(f"Synced new permission to database: {action.name}")
        else:
            logger.info("All permissions already exist in database")
