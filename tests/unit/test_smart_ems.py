import pytest
from unittest.mock import patch
from smart_ems import SmartEMS
from exceptions import SEMSFirmwareError


def test_is_firmware_version_gte():
    """Test firmware version comparison"""
    # Test equal versions
    assert SmartEMS.is_firmware_version_gte("1.6.0", 1, 6) is True

    # Test higher major version
    assert SmartEMS.is_firmware_version_gte("2.0.0", 1, 6) is True

    # Test higher minor version
    assert SmartEMS.is_firmware_version_gte("1.7.0", 1, 6) is True

    # Test lower version
    assert SmartEMS.is_firmware_version_gte("1.5.9", 1, 6) is False

    # Test lower version with string
    assert SmartEMS.is_firmware_version_gte("1.5.9-beta4", 1, 6) is False

    # Test higher version with string
    assert SmartEMS.is_firmware_version_gte("1.6.11-uac", 1, 6) is True

    # Test invalid/unknown versions
    assert SmartEMS.is_firmware_version_gte("unknown", 1, 6) is False
    assert SmartEMS.is_firmware_version_gte("", 1, 6) is False
    assert SmartEMS.is_firmware_version_gte("invalid.version", 1, 6) is False


@pytest.mark.asyncio
@patch("smart_ems.SmartEMS.get_device_by_serial")
async def test_get_device_secrets_list_unsupported_firmware(mock_get_device):
    """Test get_device_secrets_list with unsupported firmware version"""
    # Arrange
    mock_get_device.return_value = {
        "id": "12345",
        "firmwareVersion1": "1.5.0",  # Below required 1.6
    }

    # Act & Assert
    with pytest.raises(SEMSFirmwareError) as exc_info:
        await SmartEMS.get_device_secrets_list("12345")

    assert exc_info.value.status_code == 400
    assert "does not support secret management" in str(exc_info.value)
