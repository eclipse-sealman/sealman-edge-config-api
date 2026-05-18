class Platform:
    READ_DEPLOYMENT_LIST = "read_deployment_list"

    ReadPermissions = [
        READ_DEPLOYMENT_LIST,
    ]
    EditPermissions = []


class Device:
    READ = "read"
    READ_DEPLOYMENT_TAG = "read_deployment_tag"
    EDIT_DEPLOYMENT_TAG = "edit_deployment_tag"

    READ_IP_CONFIG = "read_ip_config"

    READ_MODULE_DEPLOYMENT_STATUS = "read_module_deployment_status"
    READ_MODULES = "read_modules"
    EDIT_MODULE_NAMES = "edit_module_names"
    EXECUTE_MODULE_METHOD = "execute_module_method"

    READ_CONNECTION_STATUS = "read_connection_status"

    READ_SMARTEMS_DEVICE_INFO = "read_smartems_device_info"
    READ_SMARTEMS_FIRMWARE_STATUS = "read_smartems_firmware_status"
    EDIT_SMARTEMS_DESCRIPTION = "edit_smartems_description"
    READ_SMARTEMS_CONFIG_LAN = "read_smartems_config_lan"
    EDIT_SMARTEMS_CONFIG_LAN = "edit_smartems_config_lan"
    READ_SMARTEMS_CONFIG_CELLULAR = "read_smartems_config_cellular"
    EDIT_SMARTEMS_CONFIG_CELLULAR = "edit_smartems_config_cellular"
    READ_SMARTEMS_CONFIG_NAT = "read_smartems_config_nat"
    EDIT_SMARTEMS_CONFIG_NAT = "edit_smartems_config_nat"
    EXPORT_SMARTEMS_CONFIG = "export_smartems_config"

    APPLY_SMARTEMS_TEMPLATE = "apply_smartems_template"

    READ_PASSWORD = "read_password"
    EDIT_PASSWORD = "edit_password"

    READ_CMD_STATUS = "read_cmd_status"
    READ_CMD_CONFIG = "read_cmd_config"
    READ_CMD_FW_CONFIG = "read_cmd_fw_config"

    EDIT_IP_STATIC = "edit_ip_static"

    EXECUTE_SMARTEMS_CHECK = "execute_smartems_check"

    READ_MODULE_TWIN_CONFIG = "read_module_twin_config"
    EDIT_MODULE_TWIN_CONFIG = "edit_module_twin_config"
    EDIT_MODULE_CONFIG_STATUS = "edit_module_config_status"

    DISCOVER_NETWORK = "discover_network"

    READ_LINE = "read_line"
    EDIT_LINE = "edit_line"

    ReadPermissions = [
        READ,
        READ_DEPLOYMENT_TAG,
        READ_IP_CONFIG,
        READ_MODULE_DEPLOYMENT_STATUS,
        READ_MODULES,
        READ_CONNECTION_STATUS,
        READ_SMARTEMS_DEVICE_INFO,
        READ_SMARTEMS_FIRMWARE_STATUS,
        READ_SMARTEMS_CONFIG_LAN,
        READ_SMARTEMS_CONFIG_CELLULAR,
        READ_SMARTEMS_CONFIG_NAT,
        READ_CMD_STATUS,
        READ_CMD_CONFIG,
        READ_CMD_FW_CONFIG,
        READ_MODULE_TWIN_CONFIG,
        READ_LINE,
        READ_PASSWORD,
        EXPORT_SMARTEMS_CONFIG,
    ]

    EditPermissions = [
        EDIT_DEPLOYMENT_TAG,
        EDIT_MODULE_NAMES,
        EXECUTE_MODULE_METHOD,
        EDIT_SMARTEMS_DESCRIPTION,
        EDIT_SMARTEMS_CONFIG_LAN,
        EDIT_SMARTEMS_CONFIG_CELLULAR,
        EDIT_SMARTEMS_CONFIG_NAT,
        EDIT_IP_STATIC,
        EXECUTE_SMARTEMS_CHECK,
        EDIT_MODULE_TWIN_CONFIG,
        EDIT_MODULE_CONFIG_STATUS,
        DISCOVER_NETWORK,
        EDIT_LINE,
        EDIT_PASSWORD,
        APPLY_SMARTEMS_TEMPLATE,
    ]


def _get_permission_names(resource_type):
    return {
        attribute_name: attribute_value
        for attribute_name, attribute_value in vars(resource_type).items()
        if attribute_name.isupper() and isinstance(attribute_value, str)
    }


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


_validate_unique_permission_names(Platform, Device)
