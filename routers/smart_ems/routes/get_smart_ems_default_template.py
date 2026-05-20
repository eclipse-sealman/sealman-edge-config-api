import logging

from jsonschema import ValidationError
from exceptions import InvalidInputError
from routers.smart_ems.device_type_config_repo import DeviceTypeConfigRepo
from routers.smart_ems.schemas import DefaultSmartEMSTemplate, DeviceConfig
from routers.smart_ems.template_configurator import get_configuration_name


logger = logging.getLogger("EdgeConfigAPI")


async def get_smart_ems_default_template(
    device_type: str, hardware_version: str
):
    if device_type is None:
        raise InvalidInputError("'device_type' parameter not provided.", 400)

    if hardware_version is None:
        raise InvalidInputError("'hardware_version' parameter not provided.", 400)

    config = await DeviceTypeConfigRepo.get_default_config()

    device_types_config_map = config.get("deviceTypes", None)

    if device_types_config_map is None:
        raise InvalidInputError("Missing configuration", 400)

    device_type_config = device_types_config_map.get(device_type, None)

    if device_type_config is None:
        raise InvalidInputError(f"Missing configuration for device type: {device_type}", 400)

    template_name = get_configuration_name(device_type_config, hardware_version)

    try:
        parsed_data = DeviceConfig.model_validate(device_type_config)
    except ValidationError as e:
        raise ValidationError(f"Validation error while parsing DefaultSmartEMSTemplate:{e}", 400 )

    return DefaultSmartEMSTemplate(
        deviceType=device_type,
        hardwareVersion=hardware_version,
        defaultConfig=parsed_data,
        templateName=template_name,
    )
