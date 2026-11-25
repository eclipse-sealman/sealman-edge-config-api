from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.models.password_renewal import PasswordRenewalTask, PasswordRenewalTaskStatus
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from exceptions import SEMSError, UnmatchedDependency
from routers.password.service import get_password_info, renew_password
from routers.smart_ems.password_renewal_task_processor import process_password_renewal_task
from routers.smart_ems.routes.post_smart_ems_device_secret_renew import (
    post_smart_ems_device_secret_renew,
)


@pytest.mark.parametrize("already_pending", [False, True])
async def test_manual_renewal_is_idempotent_when_upstream_pending(already_pending):
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    with (
        patch("routers.password.service.SmartEMS.get_device_secret", return_value={
            "id": 456, "forceRenewal": already_pending,
        }),
        patch("routers.password.service.SmartEMS.force_renew_device_secret") as renew,
    ):
        assert await post_smart_ems_device_secret_renew("eg-123456789", repo) is None
    assert renew.await_count == int(not already_pending)
    repo.cancel_scheduled_tasks_for_device.assert_awaited_once_with("eg-123456789")


@pytest.mark.parametrize("secret,error", [
    (None, UnmatchedDependency),
    ({"id": 456}, SEMSError),
])
async def test_manual_failure_preserves_scheduled_task(secret, error):
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    with (
        patch("routers.password.service.SmartEMS.get_device_secret", return_value=secret),
        patch("routers.password.service.SmartEMS.force_renew_device_secret",
              side_effect=SEMSError("synthetic upstream failure", 403)),
    ):
        with pytest.raises(error):
            await renew_password("eg-123456789", repo)
    repo.cancel_scheduled_tasks_for_device.assert_not_awaited()


async def test_worker_does_not_retry_canceled_or_claimed_task():
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    repo.get_pending_task.return_value = None
    task = PasswordRenewalTask(task_id="task", device_id="eg-123456789")
    with patch("routers.password.service.SmartEMS.get_device_secret") as get:
        await process_password_renewal_task(repo, task, datetime.now(timezone.utc))
    get.assert_not_awaited()


async def test_worker_failure_is_sanitized_and_retriable(caplog):
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    task = PasswordRenewalTask(task_id="task", device_id="eg-123456789")
    repo.get_pending_task.return_value = task
    with patch(
        "routers.password.service.SmartEMS.get_device_secret",
        side_effect=SEMSError("SECRET-SHOULD-NOT-APPEAR", 403),
    ):
        await process_password_renewal_task(repo, task, datetime.now(timezone.utc))
    repo.update_task_error.assert_awaited_once_with(
        task, "Password renewal request failed (HTTP 403)."
    )
    repo.complete_task.assert_not_awaited()
    assert "SECRET-SHOULD-NOT-APPEAR" not in caplog.text


async def test_info_does_not_expose_historic_error_or_claim_rotation():
    now = datetime.now(timezone.utc)
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    repo.get_latest_task_for_device.return_value = PasswordRenewalTask(
        task_id="task", device_id="eg-123456789", scheduled_time=now,
        status=PasswordRenewalTaskStatus.COMPLETED, error_count=1,
        latest_error="SECRET-SHOULD-NOT-APPEAR", created_at=now, updated_at=now,
    )
    with patch("routers.password.service.SmartEMS.get_device_secret", return_value={
        "id": 456, "forceRenewal": True, "deviceTypeSecret": {},
        "createdAt": now.isoformat(),
    }):
        info = await get_password_info("eg-123456789", repo)
    assert info.forceRenewal is True
    assert info.renewalTask.status == "Completed"
    assert "SECRET-SHOULD-NOT-APPEAR" not in info.model_dump_json()
