from datetime import datetime, timedelta, timezone
import logging

from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from helper import validate_and_extract_serial_number
from constants import DEVICE_AUTHENTICATION_SECRET_KEY
from exceptions import UnmatchedDependency
from routers.smart_ems.schemas import DeviceSecretValue
from smart_ems import SmartEMS


logger = logging.getLogger("EdgeConfigAPI")


async def post_smart_ems_device_secret_request(
    device: str,
    renew_task_repo: PasswordRenewalTaskRepository,
):
    
    validate_and_extract_serial_number(device)

    secret = await SmartEMS.get_device_secret(device, DEVICE_AUTHENTICATION_SECRET_KEY)
    if secret is None:
        raise UnmatchedDependency(  
            "Device Type does not have a device authentication secret", status_code=400
        )

    secret_id = secret.get("id")
    device_secret = await SmartEMS.show_device_secret(secret_id)

    # Initiate secret renewal process unless force renewal is set
    force_renewal = secret.get("forceRenewal", False)
    if not force_renewal:
        await _schedule_smart_ems_device_secret_renew(device, secret_id, renew_task_repo)

    return DeviceSecretValue(
        id=device_secret.get("id"), secretValue=device_secret.get("secretValue")
    )


async def _schedule_smart_ems_device_secret_renew(
    device: str,
    secret_id: int,
    renew_task_repo: PasswordRenewalTaskRepository,
):
    now = datetime.now(timezone.utc)
    task_schedule_time = now.replace(hour=23, minute=59, second=59, microsecond=0)

    if now.hour >= 23:
        task_schedule_time += timedelta(days=1)

    pending_count = await renew_task_repo.get_pending_tasks_for_device_count(device)

    if pending_count > 0:
        if now.hour < 23:
            # If the current time is before 23:00, and there is a pending task for the device, we do not schedule a new task
            logger.info(f"Pending task for device {device} already exists, not scheduling a new one.")
            return
        else:
            # If the current time is after 23:00, cancel all pending tasks for the device
            # and schedule a new task for the next day
            await renew_task_repo.cancel_scheduled_tasks_for_device(device)

    task_id = await renew_task_repo.schedule_task(device, secret_id, task_schedule_time)

    logger.info(f"Scheduled task {task_id} added.")
