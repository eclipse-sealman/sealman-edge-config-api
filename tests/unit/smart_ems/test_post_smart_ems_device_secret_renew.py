import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from exceptions import UnmatchedDependency
from routers.smart_ems.routes.post_smart_ems_device_secret_renew import (
    post_smart_ems_device_secret_renew,
)


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_renew.SmartEMS.force_renew_device_secret"
)
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_renew.SmartEMS.get_device_secret"
)
async def test_post_smart_ems_device_secret_renew_success(
    mock_get_device_secret, mock_force_renew
):
    """Test successful device secret renewal"""
    # Arrange
    device = "test-device-123"
    mock_secret = {
        "id": 456,
        "deviceTypeSecret": {"name": "admin"},
        "forceRenewal": False,
    }

    mock_get_device_secret.return_value = mock_secret
    mock_force_renew.return_value = {"success": True}
    mock_repo = AsyncMock()

    # Act
    await post_smart_ems_device_secret_renew(device, mock_repo)

    # Assert
    # Verify the correct sequence of method calls
    mock_get_device_secret.assert_called_once_with(device, "admin")
    mock_force_renew.assert_called_once_with(456)
    mock_repo.cancel_scheduled_tasks_for_device.assert_awaited_once_with(device)


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_renew.SmartEMS.get_device_secret"
)
async def test_post_smart_ems_device_secret_renew_no_secret_found(
    mock_get_device_secret,
):
    """Test when device secret is not found"""
    # Arrange
    device = "test-device-no-secret"
    mock_get_device_secret.return_value = None

    # Act & Assert
    with pytest.raises(UnmatchedDependency) as exc_info:
        await post_smart_ems_device_secret_renew(device, AsyncMock())

    assert exc_info.value.status_code == 400
    assert f"Device {device} does not have a device authentication secret" in str(
        exc_info.value
    )
    mock_get_device_secret.assert_called_once_with(device, "admin")


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_renew.SmartEMS.force_renew_device_secret"
)
@patch(
    "routers.smart_ems.routes.post_smart_ems_device_secret_renew.SmartEMS.get_device_secret"
)
async def test_post_smart_ems_device_secret_renew_force_renew_exception(
    mock_get_device_secret, mock_force_renew
):
    """Test when force_renew_device_secret raises an exception"""
    # Arrange
    device = "test-device-error"
    mock_secret = {"id": 123, "deviceTypeSecret": {"name": "admin"}}

    mock_get_device_secret.return_value = mock_secret
    mock_force_renew.side_effect = Exception("SEMS API Error")
    mock_repo = AsyncMock()

    # Act & Assert
    with pytest.raises(Exception) as exc_info:
        await post_smart_ems_device_secret_renew(device, mock_repo)

    assert "SEMS API Error" in str(exc_info.value)

    # Verify that get_device_secret and force_renew were called but cancel_tasks was not
    mock_get_device_secret.assert_called_once_with(device, "admin")
    mock_force_renew.assert_called_once_with(123)
    # cancel_tasks should not be called if force_renew fails
    mock_repo.cancel_scheduled_tasks_for_device.assert_not_called()
