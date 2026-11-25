from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
import logging
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from db.models.password_renewal import PasswordRenewalTask
from db.session import AsyncSessionLocal, get_repository
from routers.password.service import request_upstream_renewal, renewal_error_message


logger = logging.getLogger("EdgeConfigAPI")


@dataclass(frozen=True)
class PasswordRenewalTaskReference:
    task_id: str
    device_id: str


async def process_password_renewal_task(
    repository: PasswordRenewalTaskRepository,
    task: PasswordRenewalTask | PasswordRenewalTaskReference,
    current_time: datetime,
):
    # The device row lock serializes workers, manual renewal and rescheduling.
    # Re-read after acquiring it: another worker or request may have changed this task.
    async with repository.lock_device(task.device_id):
        pending = await repository.get_pending_task(task.task_id, current_time)
        if pending is None:
            return
        try:
            await request_upstream_renewal(pending.device_id)
        except Exception as error:
            diagnostic = renewal_error_message(error)
            logger.warning("Password renewal task %s: %s", pending.task_id, diagnostic)
            await repository.update_task_error(pending, diagnostic)
        else:
            await repository.complete_task(pending)


async def process_password_renewal_tasks():
    current_time = datetime.now(timezone.utc)
    renew_task_repo_factory = get_repository(PasswordRenewalTaskRepository)
    async with AsyncSessionLocal() as session:
        repository = renew_task_repo_factory(session)
        pending_tasks = await repository.get_pending_tasks(current_time)
        # Keep identifiers outside the ORM session: rollback expires ORM state.
        tasks = [
            PasswordRenewalTaskReference(task.task_id, task.device_id)
            for task in pending_tasks
        ]

    if len(tasks) > 0:
        logger.info(
            f"Processing {len(tasks)} password renewal tasks at {current_time.isoformat()}"
        )

    for task in tasks:
        try:
            async with AsyncSessionLocal() as session:
                repository = renew_task_repo_factory(session)
                await process_password_renewal_task(repository, task, current_time)
        except Exception as error:
            logger.error(
                "Password renewal task %s for device %s could not be processed (%s)",
                task.task_id, task.device_id, type(error).__name__,
            )

    # Purge old completed tasks after processing
    await purge_completed_tasks()


async def purge_completed_tasks():
    """Delete completed password renewal tasks older than 1 year"""
    current_time = datetime.now(timezone.utc)
    one_year_ago = current_time - timedelta(days=365)

    try:
        async with AsyncSessionLocal() as session:
            renew_task_repo_factory = get_repository(PasswordRenewalTaskRepository)
            repository = renew_task_repo_factory(session)
            deleted_count = await repository.purge_completed_tasks(one_year_ago)
            if deleted_count > 0:
                logger.info(f"Successfully deleted {deleted_count} old password renewal tasks")
            return deleted_count
    except Exception as e:
        logger.error(f"Error during cleanup of old password renewal tasks: {e}")
        raise