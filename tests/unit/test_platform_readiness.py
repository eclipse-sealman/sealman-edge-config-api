from routers.platform_configuration.readiness import missing_required_variables

SEMS_SET = {
    "sems_device_user": "u",
    "sems_device_password": "p",
    "sems_device_url": "sems.example.com",
    "sems_polling_interval": "600",
    "sems_edgegatewayvcc": "false",
}


def test_sems_variables_fall_back_to_twig_defaults():
    assert missing_required_variables({}, azure_iot_edge_enabled=False) == []


def test_certificate_and_custom_variables_are_never_required():
    assert missing_required_variables({"sems_certificate": ""}, azure_iot_edge_enabled=False) == []


def test_iotedge_variables_only_required_when_enabled():
    assert missing_required_variables(SEMS_SET, azure_iot_edge_enabled=False) == []
    assert missing_required_variables(SEMS_SET, azure_iot_edge_enabled=True) == [
        "iotedge_registry_username",
        "iotedge_registry_password",
        "iotedge_dps_id_scope",
    ]


def test_iotedge_complete_when_all_set():
    variables = {
        **SEMS_SET,
        "iotedge_registry_username": "u",
        "iotedge_registry_password": "p",
        "iotedge_dps_id_scope": "0ne123",
    }
    assert missing_required_variables(variables, azure_iot_edge_enabled=True) == []


def test_blank_value_counts_as_missing_without_default():
    variables = {**SEMS_SET, "iotedge_dps_id_scope": "  "}
    assert "iotedge_dps_id_scope" in missing_required_variables(variables, azure_iot_edge_enabled=True)
