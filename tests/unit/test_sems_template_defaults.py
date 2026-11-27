from bootstrap.template_defaults import read_sems_connection_defaults


def test_reads_sems_connection_defaults_from_twig():
    defaults = read_sems_connection_defaults()

    assert defaults == {
        "sems_device_user": "edgeGateway",
        "sems_device_password": "123456",
        "sems_device_url": "sems.localhost",
        "sems_polling_interval": "600",
        "sems_edgegatewayvcc": "false",
        "sems_certificate": "",
    }
