import pytest
import json
from pathlib import Path

from routers.smart_ems.routes.get_smart_ems_config_port_forwarding import (
    get_smart_ems_config_port_forwarding,
)


@pytest.fixture
def mock_device_info():
    mock_device_info = json.loads(Path("mocks/mock_device_info.json").read_text())
    yield mock_device_info


@pytest.mark.asyncio
async def test_get_smart_ems_config_portforwarding_success(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "port_forwarding_settings",
            "variableValue": json.dumps(
                {
                    "rules": [
                        {
                            "name": "ssh",
                            "interface": "lan2",
                            "srcPort": 22,
                            "destAddr": "10.0.0.5",
                            "destPort": 22,
                        }
                    ]
                }
            ),
            "variableType": "jsonObject",
        }
    ]

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    result = await get_smart_ems_config_port_forwarding("test_device")

    assert len(result.rules) == 1
    assert result.rules[0].name == "ssh"
    assert result.rules[0].interface == "lan2"
    assert result.rules[0].srcPort == 22
    assert str(result.rules[0].destAddr) == "10.0.0.5"
    assert result.rules[0].destPort == 22


@pytest.mark.asyncio
async def test_get_smart_ems_config_portforwarding_no_rules(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "port_forwarding_settings",
            "variableValue": json.dumps({"rules": []}),
            "variableType": "jsonObject",
        }
    ]

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    result = await get_smart_ems_config_port_forwarding("test_device")

    assert result.rules == []


@pytest.mark.asyncio
async def test_get_smart_ems_config_portforwarding_invalid_format(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "port_forwarding_settings",
            "variableValue": "invalid json",
            "variableType": "jsonObject",
        }
    ]

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    result = await get_smart_ems_config_port_forwarding("test_device")

    assert result.rules == []
