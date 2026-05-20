import pytest
import json
from pathlib import Path

from exceptions import SEMSError
from routers.smart_ems.routes.post_smart_ems_config_port_forwarding import (
    post_smart_ems_config_port_forwarding
)
from routers.smart_ems.schemas import PortForwardingConfig, PortForwardingRule


@pytest.fixture
def mock_device_info():
    mock_device_info = json.loads(
        Path('mocks/mock_device_info.json').read_text())
    yield mock_device_info
    mock_device_info = "Torn down - invalid value"


@pytest.mark.asyncio
async def test_post_smart_ems_config_portforwarding_success(mock_device_info, mocker):
    mock_device_info["variables"] = []

    mock_config = PortForwardingConfig(
        rules=[
            PortForwardingRule(
                name="ssh",
                interface="lan2",
                srcPort=22,
                destAddr="10.0.0.5",
                destPort=22,
            )
        ]
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mock_edit_device = mocker.patch("smart_ems.SmartEMS.edit_device", return_value=True)

    await post_smart_ems_config_port_forwarding("test_device", mock_config)

    body = mock_edit_device.call_args[0][1]
    pf_var = next(v for v in body["variables"] if v["name"] == "port_forwarding_settings")
    settings = json.loads(pf_var["variableValue"])

    assert body["reinstallConfig1"] is True
    assert pf_var["variableType"] == "jsonObject"
    assert settings == {
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


@pytest.mark.asyncio
async def test_post_smart_ems_config_portforwarding_duplicate_name(mock_device_info, mocker):
    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    mock_config = PortForwardingConfig(
        rules=[
            PortForwardingRule(
                name="rule1", interface="lan1", srcPort=8080, destAddr="192.168.1.10", destPort=80
            ),
            PortForwardingRule(
                name="rule1", interface="lan2", srcPort=8081, destAddr="192.168.1.11", destPort=81
            ),
        ]
    )

    with pytest.raises(SEMSError):
        await post_smart_ems_config_port_forwarding("test_device", mock_config)


@pytest.mark.asyncio
async def test_post_smart_ems_config_portforwarding_edit_failure(mock_device_info, mocker):
    mock_device_info["variables"] = []
    mock_config = PortForwardingConfig(rules=[])

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mocker.patch("smart_ems.SmartEMS.edit_device", return_value=False)

    with pytest.raises(SEMSError):
        await post_smart_ems_config_port_forwarding("test_device", mock_config)
