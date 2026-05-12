import pytest
from datetime import datetime
from unittest.mock import patch
from exceptions import SEMSFirmwareError
from routers.smart_ems.routes.get_smart_ems_secret_info import get_smart_ems_secret_info
from routers.smart_ems.schemas import DeviceSecretInformation


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.get_smart_ems_secret_info.SmartEMS.get_device_secret")
async def test_get_secret_info_success_with_secret(mock_get_device_secret):
    """Test successful retrieval when device has a secret"""
    # Arrange
    mock_secret = {
        "id": 123,
        "deviceTypeSecret": {"secretValueRenewAfterDays": 30},
        "forceRenewal": True,
        "createdAt": "2023-01-01T10:00:00+00:00",
        "updatedAt": "2023-01-15T15:30:00+00:00",
    }
    mock_get_device_secret.return_value = mock_secret

    # Act
    result = await get_smart_ems_secret_info("12345")

    # Assert
    assert isinstance(result, DeviceSecretInformation)
    assert result.deviceTypeHasAuthSecret is True
    assert result.secretValueRenewAfterDays == 30
    assert result.forceRenewal is True
    assert result.id == 123
    assert result.secretCreatedAt == datetime.fromisoformat("2023-01-01T10:00:00+00:00")
    assert result.secretUpdatedAt == datetime.fromisoformat("2023-01-15T15:30:00+00:00")
    assert result.error is None

    # Verify the correct parameters were passed
    mock_get_device_secret.assert_called_once_with("12345", "admin")


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.get_smart_ems_secret_info.SmartEMS.get_device_secret")
async def test_get_secret_info_success_no_secret(mock_get_device_secret):
    """Test successful retrieval when device has no secret"""
    # Arrange
    mock_get_device_secret.return_value = None

    # Act
    result = await get_smart_ems_secret_info("12345")

    # Assert
    assert isinstance(result, DeviceSecretInformation)
    assert result.deviceTypeHasAuthSecret is False
    assert result.secretValueRenewAfterDays == 0  # Default value
    assert result.forceRenewal is False  # Default value
    assert result.id is None
    assert result.secretCreatedAt is None
    assert result.secretUpdatedAt is None
    assert result.error is None

    # Verify the correct parameters were passed
    mock_get_device_secret.assert_called_once_with("12345", "admin")


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.get_smart_ems_secret_info.SmartEMS.get_device_secret")
async def test_get_secret_info_with_updated_at_none(mock_get_device_secret):
    """Test when secret exists but updatedAt is None"""
    # Arrange
    mock_secret = {
        "id": 456,
        "deviceTypeSecret": {"secretValueRenewAfterDays": 60},
        "forceRenewal": False,
        "createdAt": "2023-02-01T12:00:00+00:00",
        "updatedAt": None,
    }
    mock_get_device_secret.return_value = mock_secret

    # Act
    result = await get_smart_ems_secret_info("67890")

    # Assert
    assert result.deviceTypeHasAuthSecret is True
    assert result.secretValueRenewAfterDays == 60
    assert result.forceRenewal is False
    assert result.id == 456
    assert result.secretCreatedAt == datetime.fromisoformat("2023-02-01T12:00:00+00:00")
    assert result.secretUpdatedAt is None
    assert result.error is None


@pytest.mark.asyncio
@patch("routers.smart_ems.routes.get_smart_ems_secret_info.SmartEMS.get_device_secret")
async def test_get_secret_info_sems_firmware_error(mock_get_device_secret):
    """Test when SEMSFirmwareError is raised"""
    # Arrange
    error_message = "Device firmware: 1.5.0 does not support secret management"
    mock_get_device_secret.side_effect = SEMSFirmwareError(error_message, 400)

    # Act
    result = await get_smart_ems_secret_info("22222")

    # Assert
    assert isinstance(result, DeviceSecretInformation)
    assert result.deviceTypeHasAuthSecret is False
    assert result.secretCreatedAt is None
    assert result.secretUpdatedAt is None
    assert result.error == error_message
    assert result.id is None
    assert result.forceRenewal is False
    assert result.secretValueRenewAfterDays == 0

    # Verify the correct parameters were passed
    mock_get_device_secret.assert_called_once_with("22222", "admin")


@pytest.mark.asyncio
@patch(
    "routers.smart_ems.routes.get_smart_ems_secret_info.DEVICE_AUTHENTICATION_SECRET_KEY",
    "custom_key",
)
@patch("routers.smart_ems.routes.get_smart_ems_secret_info.SmartEMS.get_device_secret")
async def test_get_secret_info_with_custom_secret_key(mock_get_device_secret):
    """Test that the function uses the correct secret key constant"""
    # Arrange
    mock_get_device_secret.return_value = None

    # Act
    result = await get_smart_ems_secret_info("55555")

    # Assert
    assert result.deviceTypeHasAuthSecret is False
    mock_get_device_secret.assert_called_once_with("55555", "custom_key")


@pytest.mark.asyncio
async def test_device_secret_information_schema_validation():
    """Test that returned object conforms to DeviceSecretInformation schema"""
    # This test ensures the function returns a properly structured object
    with patch(
        "routers.smart_ems.routes.get_smart_ems_secret_info.SmartEMS.get_device_secret"
    ) as mock_get_secret:
        mock_get_secret.return_value = {
            "id": 999,
            "deviceTypeSecret": {"secretValueRenewAfterDays": 90},
            "forceRenewal": True,
            "createdAt": "2023-05-01T10:00:00+00:00",
            "updatedAt": "2023-05-10T12:00:00+00:00",
        }

        result = await get_smart_ems_secret_info("test_device")

        # Test that we can serialize/deserialize (validates schema)
        serialized = result.model_dump()
        recreated = DeviceSecretInformation(**serialized)

        assert recreated.deviceTypeHasAuthSecret == result.deviceTypeHasAuthSecret
        assert recreated.id == result.id
        assert recreated.secretCreatedAt == result.secretCreatedAt
        assert recreated.secretUpdatedAt == result.secretUpdatedAt
