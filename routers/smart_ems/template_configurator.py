import datetime
import logging
import re
from smart_ems import SmartEMS
from routers.smart_ems.schemas import ApplyDefaultTemplateResult
from exceptions import SEMSFirmwareError, SEMSTemplateError


EAS_INIT_LABEL = "eas_init_at"
EAS_UPDATE_LABEL = "eas_update_at"


def get_configuration_name(type_config: dict) -> str:
    return f"{type_config.get('configurationVersion')} [FW: {type_config.get('firmwareVersion')}]"


def validate_minimum_firmware(
    current_firmware: str | None, minimum_firmware: str | None
):
    if minimum_firmware is None:
        return

    if current_firmware is None:
        raise SEMSFirmwareError(
            f"Device firmware version is not set, minimum required firmware version is [{minimum_firmware}]",
            400
        )

    def version_tuple(v):
        # Strip non-numeric suffixes like _rc2, -beta, etc.
        # Extract numeric version parts (e.g., "1.9.2" from "1.9.2_rc2")
        numeric_version = re.match(r'^(\d+(?:\.\d+)*)', v)
        if not numeric_version:
            raise SEMSFirmwareError(
                f"Invalid firmware version format: [{v}]",
                400
            )
        return tuple(map(int, numeric_version.group(1).split(".")))

    if version_tuple(current_firmware) < version_tuple(minimum_firmware):
        raise SEMSFirmwareError(
            f"Device firmware version [{current_firmware}] is lower than the minimum required firmware version [{minimum_firmware}]",
            400
        )


def validate_template_data(template_data: dict):
    production_template = template_data.get("productionTemplate")
    if production_template is None:
        raise SEMSTemplateError(
            f"Template [{template_data.get('name')}] doesn't have a production template.",
            400
        )

    config = production_template.get("config1")
    if config is None:
        raise SEMSTemplateError(
            f"Template [{template_data.get('name')}] production template doesn't have config.",
            400
        )


class TemplateConfigurator:
    def __init__(
        self,
        sems_api: SmartEMS,
        initial_config: bool,
        eas_init_time=datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
    ):
        self.eas_init_time = eas_init_time
        self.initial_config = initial_config
        self.initial_variables = (
            [{"name": EAS_INIT_LABEL, "variableValue": self.eas_init_time}]
            if initial_config
            else [{"name": EAS_UPDATE_LABEL, "variableValue": self.eas_init_time}]
        )
        self.sems_api = sems_api
        self.template_set = {}

    async def configure_template(
        self, device: dict, config: dict
    ) -> ApplyDefaultTemplateResult:
        self.config = config
        self.__set_device(device)
        self.__set_config()

        body = self.__create_request_body()

        template_data = self.template_set.get(self.configuration_name)
        if template_data is None:
            template_data = await self.sems_api.get_template_by_template_name(
                self.configuration_name
            )
            self.template_set[self.configuration_name] = template_data

        validate_template_data(template_data)

        self.__populate_template_fields(body, template_data.get("id"))
        self.__combine_access_tags(body)

        body["reinstallFirmware1"] = (
            self.device_info.get("firmwareVersion1")
            != self.type_config["firmwareVersion"]
        )

        if body["reinstallFirmware1"]:
            validate_minimum_firmware(
                self.device_info.get("firmwareVersion1"),
                self.type_config.get("minimumFirmareVersion"),
            )

        self.__populate_description(body)
        self.__populate_variables(body, self.type_config)

        await self.sems_api.edit_device(self.device_info["id"], body)

        return ApplyDefaultTemplateResult(
            deviceId=self.device_id,
            deviceTemplateName=self.configuration_name,
            initTime=f"{self.eas_init_time} UTC",
        )

    def __set_config(self):
        type_config = self.config["deviceTypes"].get(f"{self.device_type_id}")
        if type_config is None:
            raise SEMSTemplateError(
                f"Missing default configuration for device_type_id [{self.device_type_id}]",
                400
            )

        self.type_config = type_config

        # Get configuration from SEMS
        self.configuration_name = get_configuration_name(type_config)
        logging.info(f"Configuration name: {self.configuration_name}")

    def __set_device(self, device: dict):
        self.device_id = device["serialNumber"]
        self.device_info = device
        self.device_type_id = self.device_info["deviceType"]["id"]

    def __populate_variables(self, body, config: dict):
        variables = self.initial_variables.copy()
        default_variables = config.get("definedVariables")
        if default_variables is not None:
            variables.extend(default_variables)
        variables_dict = {variable["name"]: variable for variable in variables}
        # existing variables that are not overriden
        existing_variables = [
            {"name": variable["name"], "variableValue": variable["variableValue"]}
            for variable in self.device_info.get("variables", [])
            if variables_dict.get(variable["name"]) is None
        ]
        variables.extend(existing_variables)
        body["variables"] = variables

    def __create_request_body(self):
        body = {
            "serialNumber": self.device_info["serialNumber"],
            "name": self.device_info["serialNumber"],
            "description": self.device_info.get("description"),
            "endorsementKey": self.device_info.get("endorsementKey"),
            "registrationId": self.device_info.get("registrationId"),
            "hardwareVersion": self.device_info.get("hardwareVersion"),
            "virtualSubnetCidr": self.device_info.get("virtualSubnetCidr"),
            "masqueradeType": self.device_info.get("masqueradeType"),
            "labels": [
                x["id"] for x in self.device_info["labels"]
            ],  # Labels array contains id and representation. We need only array of ids
        }
        return body

    def __populate_template_fields(self, body, template_id: int):
        body["template"] = template_id
        body["reinstallConfig1"] = self.type_config.get("reinstallConfig") or True
        body["requestConfigData"] = self.type_config.get("requestConfigData") or True
        body["enabled"] = self.type_config.get("enabled") or True
        body["staging"] = self.type_config.get("staging") or False

    def __populate_description(self, body):
        device_description = self.device_info.get("description")
        device_description_if_empty = self.type_config.get("deviceDescriptionIfEmpty")

        if device_description is None:
            device_description = (
                device_description_if_empty or f"EAS init at [{self.eas_init_time}]"
            )

        body["description"] = device_description

    def __combine_access_tags(self, body):
        current_access_tags = [x["id"] for x in self.device_info["accessTags"]]

        if self.type_config.get("accessTagIds") is not None:
            # Create union
            body["accessTags"] = list(
                set(current_access_tags) | set(self.type_config["accessTagIds"])
            )
        else:
            body["accessTags"] = current_access_tags
