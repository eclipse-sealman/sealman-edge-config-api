import pytest
import json
from pathlib import Path

from constants import LAN_EDGE_TEMPLATE_VERSIONS
from exceptions import UnmatchedDependency
from routers.smart_ems.routes.get_smart_ems_config_nat import get_smart_ems_config_nat
from routers.smart_ems.schemas import NatRule


@pytest.fixture
def mock_device_info():
    mock_device_info = json.loads(
        Path('mocks/mock_device_info.json').read_text())
    yield mock_device_info
    mock_device_info = "Torn down - invalid value"


@pytest.mark.asyncio
async def test_get_smart_ems_config_nat(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "nat_settings",
            "variableValue": json.dumps(
                {
                    "enabled": True,
                    "mappings": [
                        {
                            "name": "machine1",
                            "externalIp": "192.168.178.201",
                            "internalIp": "172.22.220.100",
                        },
                        {
                            "name": "machine2",
                            "externalIp": "192.168.178.202",
                            "internalIp": "172.22.220.101",
                        },
                    ],
                }
            ),
            "variableType": "jsonObject",
        }
    ]

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial',
                 return_value=mock_device_info)

    result = await get_smart_ems_config_nat("test_device")

    assert result.nat_enabled
    assert result.get_nat_rules() == [
        NatRule(name="machine1", extIp="192.168.178.201", intIp="172.22.220.100"),
        NatRule(name="machine2", extIp="192.168.178.202", intIp="172.22.220.101")
    ]


@pytest.mark.asyncio
async def test_get_smart_ems_config_nat_sems_disabled(mock_device_info, mocker):
    mock_device_info["enabled"] = False

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial',
                 return_value=mock_device_info)

    with pytest.raises(UnmatchedDependency) as excinfo:
        await get_smart_ems_config_nat("test_device")

    assert str(
        excinfo.value) == "the device needs to be enabled in smart-ems for this function"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_get_smart_ems_config_nat_template_unsupported(mock_device_info, mocker):
    mock_device_info["template"]["representation"] = "unsupported version"

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial',
                 return_value=mock_device_info)

    with pytest.raises(UnmatchedDependency) as excinfo:
        await get_smart_ems_config_nat("test_device")

    expected_message = (f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
                        f" template. Currently <unsupported version> is in use")
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400
