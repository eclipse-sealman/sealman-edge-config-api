from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from exceptions import UnmatchedDependency
from routers.password.service import request_password, schedule_password_renewal
from routers.smart_ems.routes.post_smart_ems_device_secret_request import (
    post_smart_ems_device_secret_request,
)


@pytest.mark.parametrize("force_renewal", [False, True])
async def test_reveal_schedules_only_without_pending_upstream_renewal(force_renewal):
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    repo.get_latest_task_for_device.return_value = None
    with (
        patch("routers.password.service.SmartEMS.get_device_secret", new_callable=AsyncMock) as get,
        patch("routers.password.service.SmartEMS.show_device_secret", new_callable=AsyncMock) as show,
        patch("routers.password.service.schedule_password_renewal", new_callable=AsyncMock) as schedule,
    ):
        get.return_value = {"id": 456, "forceRenewal": force_renewal}
        show.return_value = {"id": 456, "secretValue": "synthetic-password"}
        result = await post_smart_ems_device_secret_request("eg-123456789", repo)
    assert result.secretValue == "synthetic-password"
    assert result.renewalTask is None
    repo.lock_device.assert_called_once_with("eg-123456789")
    if force_renewal:
        schedule.assert_not_awaited()
    else:
        schedule.assert_awaited_once_with("eg-123456789", 456, repo)


async def test_no_secret_does_not_schedule():
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    with patch("routers.password.service.SmartEMS.get_device_secret", return_value=None):
        with pytest.raises(UnmatchedDependency, match="does not have"):
            await request_password("eg-123456789", repo)
    repo.schedule_task.assert_not_awaited()


@pytest.mark.parametrize("now,pending,expected,cancel,schedule", [
    ("2026-01-15T22:59:59+00:00", 0, "2026-01-15T23:59:59+00:00", False, True),
    ("2026-01-15T22:59:59+00:00", 1, None, False, False),
    ("2026-01-15T23:00:00+00:00", 1, "2026-01-16T23:59:59+00:00", True, True),
    ("2026-12-31T23:59:59+00:00", 0, "2027-01-01T23:59:59+00:00", False, True),
])
async def test_schedule_boundary_policy(now, pending, expected, cancel, schedule):
    repo = MagicMock(spec=PasswordRenewalTaskRepository)
    repo.get_pending_tasks_for_device_count.return_value = pending
    with patch("routers.password.service.datetime") as clock:
        clock.now.return_value = datetime.fromisoformat(now)
        await schedule_password_renewal("eg-123456789", 456, repo)
        clock.now.assert_called_once_with(timezone.utc)
    assert repo.cancel_scheduled_tasks_for_device.await_count == int(cancel)
    if schedule:
        repo.schedule_task.assert_awaited_once_with(
            "eg-123456789", 456, datetime.fromisoformat(expected)
        )
    else:
        repo.schedule_task.assert_not_awaited()
