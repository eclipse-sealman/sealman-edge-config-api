from datetime import datetime, timezone, timedelta
import logging
from constants import DEVICE_AUTHENTICATION_SECRET_KEY
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from db.session import AsyncSessionLocal, get_repository
from exceptions import UnmatchedDependency
from smart_ems import SmartEMS


logger = logging.getLogger("EdgeConfigAPI")


async def process_password_renewal_tasks():
    current_time = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        renew_task_repo_factory = get_repository(PasswordRenewalTaskRepository)
        repository = renew_task_repo_factory(session)
        tasks = await repository.get_pending_tasks(current_time)

        if len(tasks) > 0:
            logger.info(
                f"Processing {len(tasks)} password renewal tasks at {current_time.isoformat()}"
            )

        for task in tasks:
            try:
                secret = await SmartEMS.get_device_secret(
                    task.device_id, DEVICE_AUTHENTICATION_SECRET_KEY
                )
                if secret is None:
                    raise UnmatchedDependency(
                        f"Device {task.device_id} does not have a device authentication secret",
                        status_code=400,
                    )
                await SmartEMS.force_renew_device_secret(secret.get("id"))
                await repository.complete_task(task)
            except Exception as e:
                logger.error(
                    f"Error processing task {task.task_id} for device {task.device_id}: {e}"
                )
                await repository.update_task_error(task, str(e))

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