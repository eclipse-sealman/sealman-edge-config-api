class Platform:
    AUTHORIZATION_READ = "platform.authorization.read"
    AUTHORIZATION_WRITE = "platform.authorization.write"

    ReadPermissions = [
        AUTHORIZATION_READ,
    ]
    EditPermissions = [
        AUTHORIZATION_WRITE,
    ]

    _is_global = True
    _descriptions = {
        AUTHORIZATION_READ: "Read platform authorization data (users, teams, roles, permissions)",
        AUTHORIZATION_WRITE: "Write platform authorization data (users, teams, roles, permissions)",
    }


class Device:
    READ = "device.read"
    METADATA_WRITE = "device.metadata.write"
    DEPLOYMENT_WRITE = "device.deployment.write"
    MODULE_EXECUTE_METHOD = "device.module.execute_method"
    NETWORK_WRITE = "device.network.write"
    SMARTEMS_TEMPLATE_APPLY = "device.sems_template.apply"
    PASSWORD_READ = "device.password.read"
    PASSWORD_WRITE = "device.password.write"
    MODULE_TWIN_CONFIG_WRITE = "device.module_twin_config.write"
    NETWORK_DISCOVER = "device.network.discover"
    LINE_WRITE = "device.line.write"
    ENDPOINT_READ = "device.endpoint.read"
    ENDPOINT_WRITE = "device.endpoint.write"

    ReadPermissions = [
        READ,
        PASSWORD_READ,
        ENDPOINT_READ,
    ]

    EditPermissions = [
        METADATA_WRITE,
        DEPLOYMENT_WRITE,
        NETWORK_WRITE,
        MODULE_EXECUTE_METHOD,
        MODULE_TWIN_CONFIG_WRITE,
        NETWORK_DISCOVER,
        LINE_WRITE,
        PASSWORD_WRITE,
        SMARTEMS_TEMPLATE_APPLY,
        ENDPOINT_WRITE,
    ]

    _is_global = False
    _descriptions = {
        READ: "Read device details",
        METADATA_WRITE: "Update device metadata",
        DEPLOYMENT_WRITE: "Create or update device deployments",
        MODULE_EXECUTE_METHOD: "Execute direct methods on a device module",
        NETWORK_WRITE: "Update device network settings",
        SMARTEMS_TEMPLATE_APPLY: "Apply the default Smart EMS template to a device",
        PASSWORD_READ: "Read a device password",
        PASSWORD_WRITE: "Update a device password",
        MODULE_TWIN_CONFIG_WRITE: "Update device module twin configuration",
        NETWORK_DISCOVER: "Run device network discovery",
        LINE_WRITE: "Update device line settings",
        ENDPOINT_READ: "Read device endpoints, services and their types",
        ENDPOINT_WRITE: "Create, update or delete device endpoints, services and their types",
    }


def _get_permission_names(resource_type):
    return {
        attribute_name: attribute_value
        for attribute_name, attribute_value in vars(resource_type).items()
        if attribute_name.isupper() and isinstance(attribute_value, str)
    }


RESOURCE_TYPES = [Platform, Device]


def get_all_permissions() -> list[dict]:
    """Returns all defined permissions with their metadata (name, description, is_global)."""
    permissions = []
    for resource_type in RESOURCE_TYPES:
        for permission_name in _get_permission_names(resource_type).values():
            permissions.append({
                "name": permission_name,
                "description": resource_type._descriptions.get(permission_name, ""),
                "is_global": resource_type._is_global,
            })
    return permissions


# Validates that there are no duplicate permission names across the given resource types, 
# which could lead to conflicts in the RBAC permission check 
# since it only checks permission names without resource types. 
# Raises an exception if a duplicate is found.
def _validate_unique_permission_names(*resource_types):
    seen_permissions = {}

    for resource_type in resource_types:
        for attribute_name, permission_name in _get_permission_names(
            resource_type
        ).items():
            existing_permission = seen_permissions.get(permission_name)
            if existing_permission is not None:
                existing_group_name, existing_attribute_name = existing_permission
                raise ValueError(
                    "Duplicate permission name "
                    f"'{permission_name}' found in {existing_group_name}.{existing_attribute_name} "
                    f"and {resource_type.__name__}.{attribute_name}"
                )

            seen_permissions[permission_name] = (resource_type.__name__, attribute_name)


_validate_unique_permission_names(*RESOURCE_TYPES)
