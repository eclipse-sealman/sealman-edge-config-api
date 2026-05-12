from constants import DEVICE_AUTHENTICATION_SECRET_KEY
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from exceptions import UnmatchedDependency
from smart_ems import SmartEMS


async def post_smart_ems_device_secret_renew(
    device: str,
    renew_task_repo: PasswordRenewalTaskRepository,
):
    secret = await SmartEMS.get_device_secret(device, DEVICE_AUTHENTICATION_SECRET_KEY)
    if secret is None:
        raise UnmatchedDependency(
            f"Device {device} does not have a device authentication secret",
            status_code=400,
        )

    secret_id = secret.get("id")
    await SmartEMS.force_renew_device_secret(secret_id)

    # Cancel all scheduled tasks for the device since the secret will be renewed
    await renew_task_repo.cancel_scheduled_tasks_for_device(device)
