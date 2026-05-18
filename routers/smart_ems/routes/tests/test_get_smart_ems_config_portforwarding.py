import pytest
import json
from pathlib import Path

from routers.smart_ems.routes.get_smart_ems_config_portforwarding import (
    get_smart_ems_config_portforwarding
)


@pytest.fixture
def mock_device_info():
    mock_device_info = json.loads(
        Path('mocks/mock_device_info.json').read_text())
    yield mock_device_info
    mock_device_info = "Torn down - invalid value"


@pytest.mark.asyncio
async def test_get_smart_ems_config_portforwarding_success(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "pfwd_name_1",
            "variableValue": "rule1"
        },
        {
            "name": "pfwd_value_1",
            "variableValue": "iifname lan1 tcp dport 8080 dnat to 192.168.1.10:80"
        }
    ]

    mocker.patch(
        'smart_ems.SmartEMS.get_device_by_serial',
        return_value=mock_device_info
    )

    result = await get_smart_ems_config_portforwarding("test_device")

    assert len(result.rules) == 1
    assert result.rules[0].name == "rule1"
    assert result.rules[0].interface == "lan1"
    assert result.rules[0].srcPort == 8080
    assert str(result.rules[0].destAddr) == "192.168.1.10"
    assert result.rules[0].destPort == 80


@pytest.mark.asyncio
async def test_get_smart_ems_config_portforwarding_no_rules(mock_device_info, mocker):
    mock_device_info["variables"] = []

    mocker.patch(
        'smart_ems.SmartEMS.get_device_by_serial',
        return_value=mock_device_info
    )

    result = await get_smart_ems_config_portforwarding("test_device")

    assert result.rules == []


@pytest.mark.asyncio
async def test_get_smart_ems_config_portforwarding_invalid_format(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "pfwd_name_1",
            "variableValue": "rule1"
        },
        {
            "name": "pfwd_value_1",
            "variableValue": "invalid format"
        }
    ]

    mocker.patch(
        'smart_ems.SmartEMS.get_device_by_serial',
        return_value=mock_device_info
    )

    result = await get_smart_ems_config_portforwarding("test_device")

    # invalid regex → rule should be ignored
    assert result.rules == []
