import json
from pathlib import Path
import pytest

from constants import LAN_EDGE_TEMPLATE_VERSIONS
from exceptions import SEMSError, UnmatchedDependency
from routers.smart_ems.routes.post_smart_ems_config_nat import post_smart_ems_config_nat
from ...schemas import NatConfig, NatRule


@pytest.fixture
def mock_device_info():
    mock_device_info = json.loads(Path("mocks/mock_device_info.json").read_text())
    yield mock_device_info


@pytest.mark.asyncio
async def test_post_smart_ems_config(mock_device_info, mocker):
    mock_device_info["variables"] = []

    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201", intIp="172.22.220.100"),
            NatRule(name="machine2", extIp="192.168.178.202", intIp="172.22.220.101"),
        ],
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mock_edit_device = mocker.patch("smart_ems.SmartEMS.edit_device", return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    body = mock_edit_device.call_args[0][1]
    nat_var = next(v for v in body["variables"] if v["name"] == "nat_settings")
    nat_settings = json.loads(nat_var["variableValue"])

    assert nat_var["variableType"] == "jsonObject"
    assert nat_settings["enabled"] is True
    assert nat_settings["mappings"] == [
        {
            "name": "machine1",
            "internalIp": "172.22.220.100",
            "externalIp": "192.168.178.201",
        },
        {
            "name": "machine2",
            "internalIp": "172.22.220.101",
            "externalIp": "192.168.178.202",
        },
    ]


@pytest.mark.asyncio
async def test_post_smart_ems_config_overwrite_existing_nat_settings(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "nat_settings",
            "variableValue": '{"enabled":false,"mappings":[]}',
            "variableType": "jsonObject",
        },
        {"name": "other_setting", "variableValue": "abc", "variableType": "string"},
    ]

    mock_config = NatConfig(
        nat_enabled=False,
        nat_rules=[NatRule(name="machine1", extIp="192.168.178.201", intIp="172.22.220.100")],
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mock_edit_device = mocker.patch("smart_ems.SmartEMS.edit_device", return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    body = mock_edit_device.call_args[0][1]
    nat_var = next(v for v in body["variables"] if v["name"] == "nat_settings")
    nat_settings = json.loads(nat_var["variableValue"])

    assert nat_settings["enabled"] is False
    assert len(nat_settings["mappings"]) == 1
    assert any(v["name"] == "other_setting" for v in body["variables"])


@pytest.mark.asyncio
async def test_post_smart_ems_config_out_of_subnet_range(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {
            "name": "lan_settings",
            "variableValue": json.dumps({
                "lan2": {"dhcp": True, "ip": [], "subnet": [], "gateway": None, "dns": None},
                "lan3": {
                    "dhcp": False,
                    "ip": ["172.22.220.164"],
                    "subnet": ["23"],
                    "gateway": "172.22.220.1",
                    "dns": "8.8.8.8",
                },
            }),
            "variableType": "jsonObject",
        },
    ]

    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[NatRule(name="machine1", extIp="192.168.178.201", intIp="172.22.230.100")],
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(excinfo.value) == (
        "NAT ip address 172.22.230.100 out of range for specified LAN address 172.22.220.164 and subnet 23"
    )
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_same_ext_and_int_ip(mock_device_info, mocker):
    mock_device_info["variables"] = []
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[NatRule(name="machine1", extIp="172.22.220.100", intIp="172.22.220.100")],
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(excinfo.value) == "ExtIp and IntIp cannot be the same"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_repeat_extIp(mock_device_info, mocker):
    mock_device_info["variables"] = []
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201", intIp="172.22.220.100"),
            NatRule(name="machine2", extIp="192.168.178.201", intIp="172.22.220.101"),
        ],
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(excinfo.value) == "No repeated extIp or intIp between nat rules allowed"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_no_rules(mock_device_info, mocker):
    mock_device_info["variables"] = []

    mock_config = NatConfig(nat_enabled=False, nat_rules=[])

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mock_edit_device = mocker.patch("smart_ems.SmartEMS.edit_device", return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    body = mock_edit_device.call_args[0][1]
    nat_var = next(v for v in body["variables"] if v["name"] == "nat_settings")
    nat_settings = json.loads(nat_var["variableValue"])

    assert nat_settings == {"enabled": False, "mappings": []}


@pytest.mark.asyncio
async def test_get_smart_ems_config_nat_sems_disabled(mock_device_info, mocker):
    mock_device_info["enabled"] = False

    mock_config = NatConfig(nat_enabled=False, nat_rules=[])

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    with pytest.raises(UnmatchedDependency) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(excinfo.value) == "the device needs to be enabled in smart-ems for this function"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_nat_error(mock_device_info, mocker):
    mock_device_info["variables"] = []
    mock_config = NatConfig(nat_enabled=False, nat_rules=[])

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mocker.patch("smart_ems.SmartEMS.edit_device", return_value=False)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(excinfo.value) == "could not update device test_device"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_nat_unsupported_template(mock_device_info, mocker):
    mock_device_info["template"]["representation"] = "unsupported_template"

    mock_config = NatConfig(nat_enabled=False, nat_rules=[])

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)

    with pytest.raises(UnmatchedDependency) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    expected_message = (
        f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
        f" template. Currently <unsupported_template> is in use"
    )
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_update_nat_config_should_reinstall_config(mock_device_info, mocker):
    mock_device_info["variables"] = []
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[NatRule(name="machine1", extIp="192.168.178.201", intIp="172.22.220.100")],
    )

    mocker.patch("smart_ems.SmartEMS.get_device_by_serial", return_value=mock_device_info)
    mock_edit_device = mocker.patch("smart_ems.SmartEMS.edit_device", return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    assert mock_edit_device.call_args[0][1]["reinstallConfig1"]