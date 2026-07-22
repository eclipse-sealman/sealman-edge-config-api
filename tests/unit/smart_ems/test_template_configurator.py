import pytest

from exceptions import SEMSFirmwareError
from routers.smart_ems.template_configurator import validate_minimum_firmware


def test_validate_minimum_firmware_passes_when_current_is_higher():
    """Should not raise when current firmware is higher than minimum."""
    validate_minimum_firmware("1.8.2", "1.7.4")
    validate_minimum_firmware("2.0.0", "1.9.9")


def test_validate_minimum_firmware_passes_when_versions_equal():
    """Should not raise when current firmware equals minimum."""
    validate_minimum_firmware("1.8.2", "1.8.2")


def test_validate_minimum_firmware_passes_with_version_suffixes():
    """Should handle version strings with suffixes like _rc2, -beta, etc."""
    validate_minimum_firmware("1.9.2_rc2", "1.8.2")
    validate_minimum_firmware("1.9.2-beta", "1.8.2")
    validate_minimum_firmware("1.9.2_alpha1", "1.8.2")


def test_validate_minimum_firmware_passes_with_different_version_lengths():
    """Should handle versions with different number of components."""
    validate_minimum_firmware("1.9", "1.8.2")
    validate_minimum_firmware("1.9.0", "1.9")


def test_validate_minimum_firmware_passes_when_minimum_is_none():
    """Should not raise when minimum firmware is None (no requirement)."""
    validate_minimum_firmware("1.8.2", None)
    validate_minimum_firmware("0.0.1", None)


def test_validate_minimum_firmware_fails_when_current_is_lower():
    """Should raise SEMSFirmwareError when current firmware is lower than minimum."""
    with pytest.raises(SEMSFirmwareError) as e:
        validate_minimum_firmware("1.6.9", "1.7.4")

    assert e.value.status_code == 400
    assert "Device firmware version [1.6.9] is lower than the minimum required firmware version [1.7.4]" in str(e.value)


def test_validate_minimum_firmware_fails_when_current_is_none():
    """Should raise SEMSFirmwareError when current firmware is None but minimum is required."""
    with pytest.raises(SEMSFirmwareError) as e:
        validate_minimum_firmware(None, "1.8.2")

    assert e.value.status_code == 400
    assert "Device firmware version is not set" in str(e.value)
    assert "minimum required firmware version is [1.8.2]" in str(e.value)


def test_validate_minimum_firmware_fails_with_invalid_current_version():
    """Should raise SEMSFirmwareError when current firmware has invalid format."""
    with pytest.raises(SEMSFirmwareError) as e:
        validate_minimum_firmware("invalid", "1.8.2")

    assert e.value.status_code == 400
    assert "Invalid firmware version format: [invalid]" in str(e.value)


def test_validate_minimum_firmware_fails_with_invalid_minimum_version():
    """Should raise SEMSFirmwareError when minimum firmware has invalid format."""
    with pytest.raises(SEMSFirmwareError) as e:
        validate_minimum_firmware("1.8.2", "not-a-version")

    assert e.value.status_code == 400
    assert "Invalid firmware version format: [not-a-version]" in str(e.value)


def test_validate_minimum_firmware_fails_with_empty_version_strings():
    """Should raise SEMSFirmwareError when version strings are empty."""
    with pytest.raises(SEMSFirmwareError) as e:
        validate_minimum_firmware("", "1.8.2")

    assert e.value.status_code == 400
    assert "Invalid firmware version format: []" in str(e.value)