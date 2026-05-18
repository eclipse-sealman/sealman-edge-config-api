import json
from pathlib import Path
import pytest
from constants import LAN_EDGE_TEMPLATE_VERSIONS
from routers.smart_ems.routes.post_smart_ems_config_nat import post_smart_ems_config_nat
from exceptions import SEMSError, UnmatchedDependency
from ...schemas import NatConfig, NatRule


@pytest.fixture
def mock_device_info():
    mock_device_info = json.loads(
        Path('mocks/mock_device_info.json').read_text())
    yield mock_device_info
    mock_device_info = "Torn down - invalid value"


@pytest.mark.asyncio
async def test_post_smart_ems_config(mock_device_info, mocker):
    mock_device_info["variables"] = []

    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.220.100"),
            NatRule(name="machine2", extIp="192.168.178.202",
                    intIp="172.22.220.101")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)
    mock_edit_device = mocker.patch('smart_ems.SmartEMS.edit_device', return_value=True)
    
    await post_smart_ems_config_nat("test_device", mock_config)

    print(mock_edit_device)
    # Capture the body argument passed to SmartEMS.edit_device
    body = mock_edit_device.call_args[0][1]

    expected_variables = [
        {"name": "nat_enabled", "variableValue": "true"},
        {"name": "nat_machine_1", "variableValue": "machine1"},
        {"name": "nat_machine_1_lan2", "variableValue": "192.168.178.201"},
        {"name": "nat_machine_1_lan3", "variableValue": "172.22.220.100"},
        {"name": "nat_machine_2", "variableValue": "machine2"},
        {"name": "nat_machine_2_lan2", "variableValue": "192.168.178.202"},
        {"name": "nat_machine_2_lan3", "variableValue": "172.22.220.101"}
    ]

    assert body["variables"] == expected_variables


@pytest.mark.asyncio
async def test_post_smart_ems_config_remove_extra(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {"name": "nat_machine_1", "variableValue": "machine1"},
        {"name": "nat_machine_1_lan2", "variableValue": "192.168.178.201"},
        {"name": "nat_machine_1_lan3", "variableValue": "172.22.220.100"},
        {"name": "nat_machine_2", "variableValue": "machine2"},
        {"name": "nat_machine_2_lan2", "variableValue": "192.168.178.202"},
        {"name": "nat_machine_2_lan3", "variableValue": "172.22.220.101"},
        {"name": "nat_enabled", "variableValue": "true"}
    ]

    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.220.100")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)
    mock_edit_device = mocker.patch('smart_ems.SmartEMS.edit_device', return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    # Capture the body argument passed to SmartEMS.edit_device
    body = mock_edit_device.call_args[0][1]

    expected_variables = [
        {"name": "nat_enabled", "variableValue": "true"},
        {"name": "nat_machine_1", "variableValue": "machine1"},
        {"name": "nat_machine_1_lan2", "variableValue": "192.168.178.201"},
        {"name": "nat_machine_1_lan3", "variableValue": "172.22.220.100"}
    ]

    assert body["variables"] == expected_variables


@pytest.mark.asyncio
async def test_post_smart_ems_config_out_of_subnet_range(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {"name": "subnet_lan_3", "variableValue": "23"},
        {"name": "ip_lan_3", "variableValue": "172.22.220.164"}
    ]

    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.230.100")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    expected_message = (
        "NAT ip address 172.22.230.100 out of range for specified LAN address 172.22.220.164 and subnet 23")
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_same_ext_and_int_ip(mock_device_info, mocker):
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="172.22.220.100",
                    intIp="172.22.220.100")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    expected_message = ("ExtIp and IntIp cannot be the same")
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_repeat_extIp(mock_device_info, mocker):
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.220.100"),
            NatRule(name="machine2", extIp="192.168.178.201",
                    intIp="172.22.220.101")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    expected_message = (
        "No repeat extIp or intIp between nat rules allowed")
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_too_many_rules(mock_device_info, mocker):
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.220.100"),
            NatRule(name="machine2", extIp="192.168.178.202",
                    intIp="172.22.220.101"),
            NatRule(name="machine3", extIp="192.168.178.203",
                    intIp="172.22.220.102"),
            NatRule(name="machine4", extIp="192.168.178.204",
                    intIp="172.22.220.103"),
            NatRule(name="machine5", extIp="192.168.178.205",
                    intIp="172.22.220.104"),
            NatRule(name="machine6", extIp="192.168.178.206",
                    intIp="172.22.220.105"),
            NatRule(name="machine7", extIp="192.168.178.207",
                    intIp="172.22.220.106"),
            NatRule(name="machine8", extIp="192.168.178.208",
                    intIp="172.22.220.107"),
            NatRule(name="machine9", extIp="192.168.178.209",
                    intIp="172.22.220.108"),
            NatRule(name="machine10", extIp="192.168.178.210",
                    intIp="172.22.220.109"),
            NatRule(name="machine11", extIp="192.168.178.211",
                    intIp="172.22.220.110")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)
    
    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    expected_message = ("maximum of 10 NAT rules allowed")
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_nat_disable_nat(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {"name": "nat_machine_1", "variableValue": "machine1"},
        {"name": "nat_machine_1_lan2", "variableValue": "192.168.178.201"},
        {"name": "nat_machine_1_lan3", "variableValue": "172.22.220.100"},
        {"name": "nat_enabled", "variableValue": "true"},
        # Extra variable containing "nat_machine" not to be matched by the regex
        {"name": "other_nat_machine_setting", "variableValue": "test"}
    ]

    mock_config = NatConfig(
        nat_enabled=False,
        # No nat rules should be present in the variables of the request
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.220.100")
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)
    mock_edit_device = mocker.patch('smart_ems.SmartEMS.edit_device', return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    # Capture the body argument passed to SmartEMS.edit_device
    body = mock_edit_device.call_args[0][1]

    expected_variables = [
        {"name": "other_nat_machine_setting", "variableValue": "test"}]

    assert body["variables"] == expected_variables
            

@pytest.mark.asyncio
async def test_post_smart_ems_config_no_rules(mock_device_info, mocker):
    mock_device_info["variables"] = [
        {"name": "nat_machine_1", "variableValue": "machine1"},
        {"name": "nat_machine_1_lan2", "variableValue": "192.168.178.201"},
        {"name": "nat_machine_1_lan3", "variableValue": "172.22.220.100"},
        {"name": "nat_enabled", "variableValue": "true"}
    ]

    mock_config = NatConfig(
        nat_enabled=False,
        # Config body with no nat rules is still valid
        nat_rules=[]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)
    mock_edit_device = mocker.patch('smart_ems.SmartEMS.edit_device', return_value=True)

    await post_smart_ems_config_nat("test_device", mock_config)

    # Capture the body argument passed to SmartEMS.edit_device
    body = mock_edit_device.call_args[0][1]

    expected_variables = []

    assert body["variables"] == expected_variables


@pytest.mark.asyncio
async def test_get_smart_ems_config_nat_sems_disabled(mock_device_info, mocker):
    mock_device_info["enabled"] = False

    mock_config = NatConfig(
        nat_enabled=False,
        nat_rules=[]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)

    with pytest.raises(UnmatchedDependency) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(
        excinfo.value) == "the device needs to be enabled in smart-ems for this function"
    assert excinfo.value.status_code == 400

    mock_device_info["enabled"] = True


@pytest.mark.asyncio
async def test_post_smart_ems_config_nat_error(mock_device_info, mocker):
    mock_config = NatConfig(
        nat_enabled=False,
        nat_rules=[]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value=mock_device_info)
    mocker.patch('smart_ems.SmartEMS.edit_device', return_value=False)

    with pytest.raises(SEMSError) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    assert str(excinfo.value) == "could not update device test_device"
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_post_smart_ems_config_nat_unsupported_template(mock_device_info, mocker):
    mock_device_info["template"]["representation"] = "unsupported_template"

    mock_config = NatConfig(
        nat_enabled=False,
        nat_rules=[]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value = mock_device_info)

    with pytest.raises(UnmatchedDependency) as excinfo:
        await post_smart_ems_config_nat("test_device", mock_config)

    expected_message = (f"this function is only supported by devices using the <{LAN_EDGE_TEMPLATE_VERSIONS}>"
                        f" template. Currently <unsupported_template> is in use")
    assert str(excinfo.value) == expected_message
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_update_nat_config_should_reinstall_config(mock_device_info, mocker):
    mock_config = NatConfig(
        nat_enabled=True,
        nat_rules=[
            NatRule(name="machine1", extIp="192.168.178.201",
                    intIp="172.22.220.100"),
        ]
    )

    mocker.patch('smart_ems.SmartEMS.get_device_by_serial', return_value = mock_device_info)
    mock_edit_device = mocker.patch('smart_ems.SmartEMS.edit_device', return_value = True)

    await post_smart_ems_config_nat("test_device", mock_config)

    assert mock_edit_device.call_args[0][1]["reinstallConfig1"]