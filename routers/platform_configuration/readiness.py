from bootstrap.template_defaults import read_template_defaults

# Optional by design: sems_certificate, container registries, static compose deployments and custom variables.
SEMS_REQUIRED_VARIABLES = (
    "sems_device_user",
    "sems_device_password",
    "sems_device_url",
    "sems_polling_interval",
    "sems_edgegatewayvcc",
)
IOTEDGE_REQUIRED_VARIABLES = (
    "iotedge_registry_url",
    "iotedge_registry_username",
    "iotedge_registry_password",
    "edge_agent_image",
    "iotedge_dps_id_scope",
)
# The twig default of these is only a dummy, so it does not count as configured.
_NO_DEFAULT_FALLBACK = frozenset({"iotedge_dps_id_scope"})


def missing_required_variables(variables: dict[str, str], azure_iot_edge_enabled: bool) -> list[str]:
    """Required variables that neither carry a value nor fall back to a non-empty twig default."""
    defaults = read_template_defaults()
    required = SEMS_REQUIRED_VARIABLES + (IOTEDGE_REQUIRED_VARIABLES if azure_iot_edge_enabled else ())

    def is_set(name: str) -> bool:
        if (variables.get(name) or "").strip():
            return True
        return name not in _NO_DEFAULT_FALLBACK and bool(defaults.get(name))

    return [name for name in required if not is_set(name)]
