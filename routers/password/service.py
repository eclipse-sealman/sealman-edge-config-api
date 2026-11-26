from datetime import datetime, timedelta, timezone
import logging

from constants import DEVICE_AUTHENTICATION_SECRET_KEY
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from exceptions import APIError, SEMSError, SEMSFirmwareError, UnmatchedDependency
from helper import validate_and_extract_serial_number
from routers.password.schemas import (
    DeviceSecretInformation,
    DeviceSecretValue,
    PasswordRenewalTaskInformation,
)
from smart_ems import SmartEMS


logger = logging.getLogger("EdgeConfigAPI")


def renewal_error_message(error: Exception) -> str:
    if isinstance(error, APIError):
        return f"Password renewal request failed (HTTP {error.status_code})."
    return "Password renewal request failed; check upstream connectivity and configuration."


async def get_renewal_task_info(
    device: str, repository: PasswordRenewalTaskRepository
) -> PasswordRenewalTaskInformation | None:
    task = await repository.get_latest_task_for_device(device)
    if task is None:
        return None
    return PasswordRenewalTaskInformation(
        taskId=task.task_id,
        scheduledTime=task.scheduled_time,
        status=task.status.value,
        errorCount=task.error_count,
        # Older rows may contain unsanitized upstream exception text.
        latestError=(
            "Password renewal request failed; check server diagnostics."
            if task.latest_error else None
        ),
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


async def get_password_info(
    device: str, repository: PasswordRenewalTaskRepository | None = None
) -> DeviceSecretInformation:
    info = DeviceSecretInformation(deviceTypeHasAuthSecret=False)
    if repository is not None:
        info.renewalTask = await get_renewal_task_info(device, repository)
    try:
        secret = await SmartEMS.get_device_secret(device, DEVICE_AUTHENTICATION_SECRET_KEY)
    except SEMSFirmwareError as error:
        info.error = str(error)
        return info
    except SEMSError as error:
        logger.warning("Password information request failed in SEMS (HTTP %s)", error.status_code)
        raise SEMSError("SEMS password information request failed.", 502) from error
    if secret is not None:
        info.deviceTypeHasAuthSecret = True
        info.secretValueRenewAfterDays = secret["deviceTypeSecret"].get(
            "secretValueRenewAfterDays", 0
        )
        info.forceRenewal = secret.get("forceRenewal", False)
        info.id = secret.get("id")
        if info.id is not None:
            info.secretCreatedAt = datetime.fromisoformat(secret["createdAt"])
            info.secretUpdatedAt = (
                datetime.fromisoformat(secret["updatedAt"])
                if secret.get("updatedAt") is not None else None
            )
    return info


async def request_password(device: str, repository: PasswordRenewalTaskRepository):
    validate_and_extract_serial_number(device)
    async with repository.lock_device(device):
        try:
            secret = await SmartEMS.get_device_secret(device, DEVICE_AUTHENTICATION_SECRET_KEY)
            if secret is None:
                raise UnmatchedDependency(
                    "Device Type does not have a device authentication secret", status_code=400
                )
            secret_id = secret["id"]
            device_secret = await SmartEMS.show_device_secret(secret_id)
        except SEMSError as error:
            logger.warning("Password request failed in SEMS (HTTP %s)", error.status_code)
            raise SEMSError("SEMS password request failed.", 502) from error
        if not secret.get("forceRenewal", False):
            await schedule_password_renewal(device, secret_id, repository)
        return DeviceSecretValue(
            id=device_secret["id"],
            secretValue=device_secret["secretValue"],
            renewalTask=await get_renewal_task_info(device, repository),
        )


async def schedule_password_renewal(
    device: str, secret_id: int, repository: PasswordRenewalTaskRepository
):
    """Called while holding the device lock, including cancel/reschedule and commit."""
    now = datetime.now(timezone.utc)
    scheduled_time = now.replace(hour=23, minute=59, second=59, microsecond=0)
    if now.hour >= 23:
        scheduled_time += timedelta(days=1)
    pending_count = await repository.get_pending_tasks_for_device_count(device)
    if pending_count:
        if now.hour < 23:
            return
        await repository.cancel_scheduled_tasks_for_device(device)
    task_id = await repository.schedule_task(device, secret_id, scheduled_time)
    logger.info("Scheduled password renewal task %s", task_id)


async def request_upstream_renewal(device: str) -> None:
    secret = await SmartEMS.get_device_secret(device, DEVICE_AUTHENTICATION_SECRET_KEY)
    if secret is None:
        raise UnmatchedDependency(
            f"Device {device} does not have a device authentication secret",
            status_code=400,
        )
    if not secret.get("forceRenewal", False):
        await SmartEMS.force_renew_device_secret(secret["id"])


async def renew_password(device: str, repository: PasswordRenewalTaskRepository):
    async with repository.lock_device(device):
        try:
            await request_upstream_renewal(device)
        except Exception as error:
            logger.warning("Manual password renewal: %s", renewal_error_message(error))
            if isinstance(error, SEMSError):
                raise SEMSError("SEMS password renewal request failed.", 502) from error
            raise
        # Accepted (or already pending) is not confirmation of device-side rotation.
        await repository.cancel_scheduled_tasks_for_device(device)
