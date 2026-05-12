import pytest

from exceptions import SEMSFirmwareError
from routers.smart_ems.template_configurator import validate_minimum_firmware

def test_validate_minimum_firmware():
    # should not raise
    validate_minimum_firmware("1.8.2", "1.7.4")
    validate_minimum_firmware("1.8.2", "1.8.2")
    validate_minimum_firmware("1.8.2", None)

    with pytest.raises(SEMSFirmwareError) as e:
        validate_minimum_firmware("1.6.9", "1.7.4")

    assert e.value.status_code == 400
    assert "Device firmware version [1.6.9] is lower than the minimum required firmware version [1.7.4]" in str(e.value)