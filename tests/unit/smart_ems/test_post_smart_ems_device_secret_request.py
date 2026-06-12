import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from exceptions import UnmatchedDependency
from routers.smart_ems.routes.post_smart_ems_device_secret_request import (
    post_smart_ems_device_secret_request,
    _schedule_smart_ems_device_secret_renew,
)
from routers.smart_ems.schemas import DeviceSecretValue


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request.SmartEMS.show_device_secret"
)
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request.SmartEMS.get_device_secret"
)
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request._schedule_smart_ems_device_secret_renew"
    ,
    new_callable=AsyncMock,
)
async def test_post_smart_ems_device_secret_request_success_without_force_renewal(
    mock_schedule_renew, mock_get_device_secret, mock_show_device_secret
):
    """Test successful device secret request without force renewal"""
    # Arrange
    device = "eg-987654321"
    mock_secret = {"id": 456, "forceRenewal": False}
    mock_device_secret = {"id": 456, "secretValue": "secret123456"}

    mock_get_device_secret.return_value = mock_secret
    mock_show_device_secret.return_value = mock_device_secret
    mock_repo = AsyncMock()

    # Act
    result = await post_smart_ems_device_secret_request(device, mock_repo)

    # Assert
    assert isinstance(result, DeviceSecretValue)
    assert result.id == 456
    assert result.secretValue == "secret123456"

    # Verify method calls
    mock_get_device_secret.assert_called_once_with(device, "admin")
    mock_show_device_secret.assert_called_once_with(456)
    mock_schedule_renew.assert_awaited_once_with(device, 456, mock_repo)


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request.SmartEMS.show_device_secret"
)
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request.SmartEMS.get_device_secret"
)
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request._schedule_smart_ems_device_secret_renew"
    ,
    new_callable=AsyncMock,
)
async def test_post_smart_ems_device_secret_request_success_with_force_renewal(
    mock_schedule_renew, mock_get_device_secret, mock_show_device_secret
):
    """Test successful device secret request with force renewal (no scheduling)"""
    # Arrange
    device = "eg-123456789"
    mock_secret = {"id": 789, "forceRenewal": True}
    mock_device_secret = {"id": 789, "secretValue": "forced_secret789"}

    mock_get_device_secret.return_value = mock_secret
    mock_show_device_secret.return_value = mock_device_secret
    mock_repo = AsyncMock()

    # Act
    result = await post_smart_ems_device_secret_request(device, mock_repo)

    # Assert
    assert isinstance(result, DeviceSecretValue)
    assert result.id == 789
    assert result.secretValue == "forced_secret789"

    # Verify method calls
    mock_get_device_secret.assert_called_once_with(device, "admin")
    mock_show_device_secret.assert_called_once_with(789)
    # Should not schedule renewal when force renewal is true
    mock_schedule_renew.assert_not_called()


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_request.SmartEMS.get_device_secret"
)
async def test_post_smart_ems_device_secret_request_no_secret_found(
    mock_get_device_secret,
):
    """Test when device secret is not found"""
    # Arrange
    device = "eg-7777777777"
    mock_get_device_secret.return_value = None

    # Act & Assert
    with pytest.raises(UnmatchedDependency) as exc_info:
        await post_smart_ems_device_secret_request(device, AsyncMock())

    assert exc_info.value.status_code == 400
    assert "Device Type does not have a device authentication secret" in str(
        exc_info.value
    )
    mock_get_device_secret.assert_called_once_with(device, "admin")


# Tests for _schedule_smart_ems_device_secret_renew function


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.post_smart_ems_device_secret_request.datetime")
async def test_schedule_smart_ems_device_secret_renew_no_pending_tasks_before_23(
    mock_datetime
):
    """Test scheduling when no pending tasks exist and current time is before 23:00"""
    # Arrange
    device = "eg-7777777777"
    secret_id = 123
    current_time = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)  # 10:30 AM
    expected_schedule_time = current_time.replace(
        hour=23, minute=59, second=59, microsecond=0
    )

    mock_datetime.now.return_value = current_time
    mock_repo = AsyncMock()
    mock_repo.get_pending_tasks_for_device_count.return_value = 0
    mock_repo.schedule_task.return_value = "task-uuid-123"

    # Act
    await _schedule_smart_ems_device_secret_renew(device, secret_id, mock_repo)

    # Assert
    mock_repo.get_pending_tasks_for_device_count.assert_awaited_once_with(device)
    mock_repo.schedule_task.assert_awaited_once_with(
        device, secret_id, expected_schedule_time
    )


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.post_smart_ems_device_secret_request.datetime")
async def test_schedule_smart_ems_device_secret_renew_with_pending_tasks_before_23(
    mock_datetime
):
    """Test scheduling when pending renewal tasks exist and current time is before 23:00 - should not schedule new task"""
    # Arrange
    device = "eg-7777777777"
    secret_id = 123
    current_time = datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc)  # 10:30 AM

    mock_datetime.now.return_value = current_time
    mock_repo = AsyncMock()
    mock_repo.get_pending_tasks_for_device_count.return_value = 1

    # Act
    await _schedule_smart_ems_device_secret_renew(device, secret_id, mock_repo)

    # Assert
    mock_repo.get_pending_tasks_for_device_count.assert_awaited_once_with(device)
    # Should not schedule new task when pending tasks exist before 23:00
    mock_repo.schedule_task.assert_not_awaited()


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.post_smart_ems_device_secret_request.datetime")
async def test_schedule_smart_ems_device_secret_renew_with_pending_tasks_after_23(
    mock_datetime
):
    """Test scheduling when pending tasks exist and current time is after 23:00 - should cancel and reschedule"""
    # Arrange
    device = "eg-7777777777"
    secret_id = 123
    current_time = datetime(2025, 1, 15, 23, 30, 0, tzinfo=timezone.utc)  # 11:30 PM
    expected_schedule_time = current_time.replace(
        hour=23, minute=59, second=59, microsecond=0
    ) + timedelta(days=1)

    mock_repo = AsyncMock()
    mock_datetime.now.return_value = current_time
    mock_repo.get_pending_tasks_for_device_count.return_value = 1
    mock_repo.schedule_task.return_value = "new-task-uuid"

    # Act
    await _schedule_smart_ems_device_secret_renew(device, secret_id, mock_repo)

    # Assert
    mock_repo.get_pending_tasks_for_device_count.assert_awaited_once_with(device)
    # Should cancel all existing pending tasks for this device in one repository call
    mock_repo.cancel_scheduled_tasks_for_device.assert_awaited_once_with(device)
    # Should schedule new task for next day
    mock_repo.schedule_task.assert_awaited_once_with(
        device, secret_id, expected_schedule_time
    )


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.post_smart_ems_device_secret_request.datetime")
async def test_schedule_smart_ems_device_secret_renew_no_pending_tasks_after_23(
    mock_datetime
):
    """Test scheduling when no pending tasks exist and current time is after 23:00"""
    # Arrange
    device = "eg-7777777777"
    secret_id = 123
    current_time = datetime(2025, 1, 15, 23, 45, 0, tzinfo=timezone.utc)  # 11:45 PM
    expected_schedule_time = current_time.replace(
        hour=23, minute=59, second=59, microsecond=0
    ) + timedelta(days=1)

    mock_repo = AsyncMock()
    mock_datetime.now.return_value = current_time
    mock_repo.get_pending_tasks_for_device_count.return_value = 0
    mock_repo.schedule_task.return_value = "task-uuid-456"

    # Act
    await _schedule_smart_ems_device_secret_renew(device, secret_id, mock_repo)

    # Assert
    mock_repo.get_pending_tasks_for_device_count.assert_awaited_once_with(device)
    # Should schedule for today since no pending tasks exist
    mock_repo.schedule_task.assert_awaited_once_with(
        device, secret_id, expected_schedule_time
    )