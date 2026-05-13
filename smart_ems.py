import json
import logging
import asyncio
import re
import datetime
import yaml
from constants import SEMS_URL, SEMS_USER, SEMS_PW, FIRMWARE_VERSION_PATTERN
from async_requests import post_async, get_async, delete_async
from exceptions import SEMSError, SEMSFirmwareError
from helper import validate_and_extract_serial_number

SEMS_ERROR_MESSAGE = "Error during SmartEMS communication"
DEVICE_NOT_FOUND_MESSAGE = "Device not found"

async def init_smart_ems():
    while True:
        try:
            if SmartEMS.init_done():
                await SmartEMS.refresh_token()
            else:
                await SmartEMS.init()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception as ex:
            logging.getLogger("SmartEMS").exception("init_smart_ems encountered an error, retrying: %s", ex)
            await asyncio.sleep(5)


class SmartEMS:
    _token = None
    _refresh_token = None
    _init_done = False
    _log = logging.getLogger("SmartEMS")
    _log.setLevel(logging.INFO)
    _log_handler = logging.StreamHandler()
    _log_formatter = logging.Formatter(
        fmt="%(levelname)s:     %(asctime)s >> %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    _log_handler.setFormatter(_log_formatter)
    _log.addHandler(_log_handler)

    @classmethod
    def init_done(cls):
        return cls._init_done

    @classmethod
    async def init(cls):
        cls._log.info("Initialize Smart-EMS")
        responses = {}

        get_sems_token_url = f"{SEMS_URL}/web/api/authentication/login_check"
        token = None

        try:
            await asyncio.gather(
                post_async(
                    get_sems_token_url,
                    responses,
                    _json={"username": SEMS_USER, "password": SEMS_PW},
                ),
                return_exceptions=True,
            )
            token = responses[get_sems_token_url].json().get("token")
        except Exception as ex:
            cls._log.error(f"error while init smart-ems api: {str(ex)}")

        if token is not None:
            cls._token = token
            cls._refresh_token = (
                responses[get_sems_token_url].json().get("refreshToken")
            )
            cls._init_done = True
            cls._log.info("Smart-EMS initialized")
        else:
            cls._init_done = False
            cls._log.warning(
                f"Could not init smart-ems - try again later. SEMS_URL: {SEMS_URL}"
            )

    @classmethod
    async def refresh_token(cls):
        cls._log.debug("request new token")
        resp = {}
        uri = f"{SEMS_URL}/web/api/authentication/token/refresh"
        await asyncio.gather(
            post_async(uri, resp, _json={"refreshToken": cls._refresh_token})
        )
        if resp[uri].status_code == 200:
            cls._log.debug("token refresh successful")
            cls._token = resp[uri].json().get("token")
            cls._refresh_token = resp[uri].json().get("refreshToken")
        else:
            cls._log.error("token refresh failed")
            cls._init_done = False
            await cls.init()

    @classmethod
    async def get_device_by_serial(cls, serial: str, require_template: bool = True):
        serial = validate_and_extract_serial_number(serial)
        resp = {}
        uri1 = f"{SEMS_URL}/web/api/device/list"  # find serial based -> EG & EG+VPNCC

        await asyncio.gather(
            post_async(
                uri1,
                resp,
                _json={
                    "page": 1,
                    "rowsPerPage": 10,
                    "sorting": [{"field": "createdAt", "direction": "desc"}],
                    "filters": {
                        "serialNumber": {
                            "filterBy": "serialNumber",
                            "filterType": "equal",
                            "filterValue": serial,
                        },
                        "deviceType": {
                            "filterBy": "deviceType",
                            "filterType": "equalMultiple",
                            "filterValue": [9, 10],
                        },
                    },
                },
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )
        if resp[uri1].status_code == 200:
            results = resp[uri1].json().get("results")
            return cls._select_device_from_results(results, serial, require_template)

        else:
            raise SEMSError(
                SEMS_ERROR_MESSAGE,
                status_code=resp[uri1].status_code,
            )

    @classmethod
    def _check_if_template_is_available(cls, dev_info):
        if dev_info.get("template") is None:
            raise SEMSError(
                f"no template assigned to device: {dev_info.get('serialNumber')}",
                status_code=400,
            )

    @classmethod
    def _select_device_from_results(cls, results, serial: str, require_template: bool = True):
        # max 3 results can be received during transition from SEMS2 -> SEMS3
        if len(results) in [1, 2]:
            for device_info in results:
                name = device_info["deviceType"]["name"]

                # in case of EG+VPNCC one device_info obj contains all information
                if name == "Edge gateway with VPN Container Client":
                    if require_template:
                        cls._check_if_template_is_available(device_info)
                    return device_info

                # in case of EG and the request was not for a vpncc return EG device_info obj
                elif name == "Edge gateway":
                    return device_info
            # if loop finishes without return, conditions are uncertain
            raise SEMSError(f"uncertain conditions for device {serial}", 400)

        return None

    @classmethod
    async def get_device_list(cls):
        uri = f"{SEMS_URL}/web/api/device/list"
        cls._log.info(f"get_device_list uri: {uri}")
        request_body = {
            "sorting": [{"field": "createdAt", "direction": "desc"}],
            "filters": {
                "deviceType": {
                    "filterBy": "deviceType",
                    "filterType": "equalMultiple",
                    "filterValue": [8, 9, 10],
                }
            },
        }
        devices = await cls.get_paginated_results(uri, request_body, 300)
        return devices

    @classmethod
    async def get_paginated_results(
        cls, uri: str, request_body: dict, rows_per_page: int
    ):
        results = []
        request_body["page"] = 1
        request_body["rowsPerPage"] = rows_per_page
        resp = {}
        await post_async(
            uri,
            resp,
            _json=request_body,
            headers={"Authorization": f"Bearer {cls._token}"},
        )

        if resp[uri].status_code == 200:
            results.extend(resp[uri].json().get("results"))
            row_count = int(resp[uri].json().get("rowCount"))
        else:
            raise SEMSError(
                SEMS_ERROR_MESSAGE,
                status_code=resp[uri].status_code,
            )

        if row_count <= rows_per_page:
            return results

        total_pages = row_count // rows_per_page + 1
        cls._log.info(f"total pages: {total_pages}")

        for page in range(2, total_pages + 1):
            request_body["page"] = page
            request_body["rowsPerPage"] = rows_per_page
            cls._log.info(f"getting page: {page}")
            await post_async(
                    uri,
                    resp,
                    _json=request_body,
                    headers={"Authorization": f"Bearer {cls._token}"},
                    timeout=10,
                )
            

            if resp[uri].status_code == 200:
                results.extend(resp[uri].json().get("results"))
                page += 1
            else:
                raise SEMSError(
                    SEMS_ERROR_MESSAGE,
                    status_code=resp[uri].status_code,
                )

        return results

    @classmethod
    async def get_vpn_client_by_serial(cls, serial: str):
        # handle eg-<serial> and fht-<serial>cases
        serial = validate_and_extract_serial_number(serial)
        resp = {}
        uri = f"{SEMS_URL}/web/api/device/list"
        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json={
                    "page": 1,
                    "rowsPerPage": 10,
                    "sorting": [{"field": "createdAt", "direction": "desc"}],
                    "filters": {
                        "deviceType": {
                            "filterBy": "deviceType",
                            "filterType": "equalMultiple",
                            "filterValue": [8],
                        },
                        "name": {
                            "filterBy": "name",
                            "filterType": "like",
                            "filterValue": serial,
                        },
                    },
                },
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )

        if resp[uri].status_code == 200:
            results = resp[uri].json().get("results")
            if len(results) == 1:
                return results[0]
            else:
                raise SEMSError(
                    f"device filter for <{serial}> matches more than one device",
                    status_code=400,
                )
        else:
            raise SEMSError(
                SEMS_ERROR_MESSAGE,
                status_code=resp[uri].status_code,
            )

    @classmethod
    async def get_firmware_update_status(cls, serial: str):
        device_info = await cls.get_device_by_serial(serial)
        if device_info is None:
            raise SEMSError(DEVICE_NOT_FOUND_MESSAGE, 500)
        template_name = "not assigned"
        template = device_info["template"].get("productionTemplate")
        if template is not None:
            template_name = template.get("representation")

        device_id = device_info["id"]
        device_firmware_version = device_info.get("firmwareVersion1", "unknown")
        device_firmware_update_scheduled = device_info["reinstallFirmware1"]
        device_config_update_scheduled = device_info["reinstallConfig1"]
        device_enabled = device_info["enabled"]
        device_last_seen = device_info.get("seenAt", "never seen")
        device_template = template_name
        device_hardware_version = device_info.get("hardwareVersion", "unknown")
        resp = {}
        uri = f"{SEMS_URL}/web/api/devicecommand/list"
        body_edge_gateway_command_list = {
            "page": 1,
            "rowsPerPage": 10,
            "sorting": [
                {"field": "createdAt", "direction": "desc"},
                {"field": "id", "direction": "desc"},
            ],
            "filters": {
                "device": {
                    "filterBy": "device",
                    "filterType": "equal",
                    "filterValue": device_id,
                }
            },
        }

        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json=body_edge_gateway_command_list,
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )
        if resp[uri].status_code == 200:
            edge_command_status = []
            results = resp[uri].json().get("results")

            for cmd in results:
                cmd_name = cmd["commandName"]
                status = cmd["commandStatus"]
                created = cmd["createdAt"]
                updated = cmd["updatedAt"]
                edge_command_status.append(
                    {
                        "cmdName": cmd_name,
                        "status": status,
                        "created": created,
                        "updated": updated,
                    }
                )
            return {
                "deviceFirmwareVersion": device_firmware_version,
                "deviceEnabled": device_enabled,
                "deviceTemplate": device_template,
                "deviceLastSeen": device_last_seen,
                "deviceHardwareVersion": device_hardware_version,
                "firmwareUpdateScheduled": device_firmware_update_scheduled,
                "configUpdateScheduled": device_config_update_scheduled,
                "edgeCommandStatus": edge_command_status,
            }
        else:
            raise SEMSError(
                SEMS_ERROR_MESSAGE,
                status_code=resp[uri].status_code,
            )

    @classmethod
    def _parse_validation_errors(cls, error_response):
        """Parse validation errors and extract only the fields with actual errors"""
        if not isinstance(error_response, dict) or "errors" not in error_response:
            return error_response

        def extract_field_errors(errors_obj, path=""):
            field_errors = []

            if isinstance(errors_obj, dict):
                # Check for direct errors array
                if "errors" in errors_obj and isinstance(errors_obj["errors"], list):
                    for error in errors_obj["errors"]:
                        if isinstance(error, dict) and "message" in error:
                            field_errors.append(f"{path}: {error['message']}")

                # Check for children errors
                if "children" in errors_obj:
                    children = errors_obj["children"]
                    if isinstance(children, dict):
                        for field_name, field_errors_obj in children.items():
                            field_path = f"{path}.{field_name}" if path else field_name
                            field_errors.extend(
                                extract_field_errors(field_errors_obj, field_path)
                            )
                    elif isinstance(children, list):
                        for i, child in enumerate(children):
                            field_path = f"{path}[{i}]" if path else f"[{i}]"
                            field_errors.extend(extract_field_errors(child, field_path))

            return field_errors

        field_errors = extract_field_errors(error_response.get("errors", {}))

        if field_errors:
            return f"Validation failed for fields: {'; '.join(field_errors)}"
        else:
            return (
                f"Validation failed: {error_response.get('message', 'Unknown error')}"
            )

    @classmethod
    async def get_template_by_template_name(cls, template_name):
        resp = {}
        uri = f"{SEMS_URL}/web/api/template/list"
        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json={
                    "page": 1,
                    "rowsPerPage": 2,
                    "sorting": [],
                    "filters": {
                        "nameFilter": {
                            "filterBy": "name",
                            "filterType": "equal",
                            "filterValue": template_name,
                        }
                    },
                },
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )

        if resp[uri].status_code != 200:
            raise SEMSError(
                f"Could not find template by template_name [{template_name}] in smart-ems",
                status_code=resp[uri].status_code,
            )
        
        response_json = resp[uri].json()
        if response_json.get("results") is None:
            raise SEMSError(
                f"Results are empty for template_name [{template_name}] in smart-ems",
                status_code=400
            )
        elif len(response_json.get("results")) == 0:
            raise SEMSError(
                f"Could not find template by template_name [{template_name}] in smart-ems",
                status_code=400
            )
        elif len(response_json.get("results")) > 1:
            raise SEMSError(
                f"Multiple templates found by template_name [{template_name}] in smart-ems",
                status_code=400
            )

        return response_json.get("results")[0]


    @classmethod
    async def edit_device(cls, edge_gateway_id: str, body: dict):
        resp = {}
        uri = f"{SEMS_URL}/web/api/device/{edge_gateway_id}"
        await asyncio.gather(
            post_async(
                uri, resp, _json=body, headers={"Authorization": f"Bearer {cls._token}"}
            )
        )
        if resp[uri].status_code == 204 or resp[uri].status_code == 200:
            return True
        else:
            error_message = resp[uri].json()
            parsed_error = cls._parse_validation_errors(error_message)
            raise SEMSError(
                f"{SEMS_ERROR_MESSAGE}: {parsed_error}",
                status_code=resp[uri].status_code,
            )

    @classmethod
    async def create_device(cls, payload: dict):
        resp = {}
    
        uri = f"{SEMS_URL}/web/api/device/create"
    
        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json=payload,
                headers={"Authorization": f"Bearer {cls._token}"},
                timeout=30
            )
        )
    
        if resp[uri].status_code in [200, 201]:
            return resp[uri].json()
        else:
            error_message = resp[uri].json()
            raise SEMSError(
                f"{SEMS_ERROR_MESSAGE}: {error_message}",
                status_code=resp[uri].status_code,
            )
    
    @classmethod
    async def delete_device(cls, device_id: str):
        resp = {}
        uri = f"{SEMS_URL}/web/api/device/{device_id}"
        await asyncio.gather(
            delete_async(
                uri,
                resp,
                headers={"Authorization": f"Bearer {cls._token}"},
                timeout=30
            )
        )
        if resp[uri].status_code in [200, 204]:
            return True
        else:
            error_message = resp[uri].json()
            raise SEMSError(
                f"{SEMS_ERROR_MESSAGE}: {error_message}",
                status_code=resp[uri].status_code,
            )

    @classmethod
    async def device_config_download(cls, edge_gateway_id: str):
        resp = {}
        uri = f"{SEMS_URL}/web/api/device/{edge_gateway_id}/generate/config/primary"
        await asyncio.gather(
            get_async(uri, resp, headers={"Authorization": f"Bearer {cls._token}"})
        )
        if resp[uri].status_code == 200:
            return json.loads(resp[uri].json())
        else:
            raise SEMSError(
                f"could not generate primary config for device <{edge_gateway_id}>",
                status_code=resp[uri].status_code,
            )

    @classmethod
    async def get_devices_last_seen(
        cls, timerange_in_days, response_hook=None, max_count=10000
    ):
        resp = {}
        uri = f"{SEMS_URL}/web/api/device/list"

        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json={
                    "page": 1,
                    "rowsPerPage": 50,
                    "filters": {
                        "deviceType": {
                            "filterBy": "deviceType",
                            "filterType": "equalMultiple",
                            "filterValue": [9, 10],
                        },
                        "enabled": {
                            "filterBy": "enabled",
                            "filterType": "boolean",
                            "filterValue": True,
                        },
                    },
                },
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )

        if resp[uri].status_code == 200:
            devices_last_seen = {}
            for device in resp[uri].json().get("results"):
                # skip eg in prio of eg+vpncc
                if (
                    device["deviceType"]["id"] == 9
                    and device["serialNumber"] in devices_last_seen
                ):
                    continue

                device_last_seen = device.get("seenAt", "2022-01-01T00:00:00+00:00")
                dt_last_seen = datetime.datetime.fromisoformat(device_last_seen)
                dt_now = datetime.datetime.now(tz=datetime.timezone.utc)
                dt_delta = dt_now - dt_last_seen
                last_seen_in_range = False
                if dt_delta.days < int(timerange_in_days):
                    last_seen_in_range = True

                devices_last_seen.update(
                    {
                        device["serialNumber"]: {
                            "lastSeenAt": device_last_seen,
                            "lastSeenInRange": last_seen_in_range,
                            "lastSeenRangeInDays": timerange_in_days,
                        }
                    }
                )

            if response_hook is None:
                return devices_last_seen

            response_hook["get_devices_last_seen"] = devices_last_seen

        else:
            raise SEMSError(
                SEMS_ERROR_MESSAGE,
                status_code=resp[uri].status_code,
            )

    @classmethod
    async def get_compose_container(cls, device_id, key=None, resp=None):
        device_info = await cls.get_device_by_serial(device_id, require_template=False)
        if device_info is None:
            raise SEMSError(DEVICE_NOT_FOUND_MESSAGE, 500)
        
        config_docker = None
        config_docker_compose_files = None
        try:
            config = await cls.device_config_download(device_info.get("id"))
            config_docker = config.get("docker")
            config_docker_compose_files = config_docker.get("compose_files")
        except SEMSError as semsError:
            cls._log.info(f"Could not download device config for device <{device_id}>: {str(semsError)}")
            
        compose_containers = {}

        if config_docker is None or config_docker_compose_files is None:
            # handle await gather from calling function if requested
            if key is not None and resp is not None:
                resp.update({key: compose_containers})
            return compose_containers

        for compose_name in config_docker_compose_files:
            compose_dict = yaml.safe_load(config_docker_compose_files[compose_name])

            services = compose_dict["services"]

            for service_id in services:

                container_image = services[service_id].get("image")
                container_name = services[service_id].get("container_name", service_id)
                version = container_image.split(":")[-1]
                compose_containers[service_id] = {
                    "name": container_name,
                    "version": version,
                }

        # handle await gather from calling function if requested
        if key is not None and resp is not None:
            resp.update({key: compose_containers})
        else:
            return compose_containers

    @classmethod
    async def get_device_secrets_list(cls, device):
        resp = {}
        device_info = await cls.get_device_by_serial(device, require_template=False)
        if device_info is None:
            raise SEMSError(DEVICE_NOT_FOUND_MESSAGE, 500)

        device_id = device_info.get("id")
        firmware_string = device_info.get("firmwareVersion1", "unknown")

        if not SmartEMS.is_firmware_version_gte(firmware_string, 1, 6):
            raise SEMSFirmwareError(
                f"Device firmware: {firmware_string} does not support secret management",
                400,
            )

        uri = f"{SEMS_URL}/web/api/devicesecret/{device_id}/list"

        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json={"page": 1, "rowsPerPage": 50, "filters": {}},
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )

        if resp[uri].status_code == 200:
            return resp[uri].json()
        else:
            raise SEMSError(
                f"error getting secret list for device <{device_id}>",
                status_code=resp[uri].status_code,
            )

    @classmethod
    def is_firmware_version_gte(
        cls, firmware_string: str, min_major: int, min_minor: int
    ) -> bool:
        """
        Check if firmware version is greater than or equal to specified major.minor version

        Args:
            firmware_string: The firmware version string (e.g., "1.6.0", "1.5.3", "2.0.1")
            min_major: Minimum required major version
            min_minor: Minimum required minor version

        Returns:
            bool: True if firmware version >= min_major.min_minor.0, False otherwise
        """
        try:
            # Handle cases where firmware_string might be "unknown" or None
            if not firmware_string or firmware_string == "unknown":
                return False

            # Parse version using regex to extract major, minor, patch
            firmware_version_match = re.match(FIRMWARE_VERSION_PATTERN, firmware_string.strip())

            if not firmware_version_match:
                cls._log.warning(
                    f"Could not parse firmware version format: '{firmware_string}'"
                )
                return False

            major = int(firmware_version_match.group(1))
            minor = int(firmware_version_match.group(2))

            # Compare with min_major.min_minor.0
            if major > min_major:
                return True
            elif major == min_major and minor >= min_minor:
                return True
            else:
                return False

        except Exception as ex:
            cls._log.warning(
                f"Could not parse firmware version '{firmware_string}': {ex}"
            )
            return False

    @classmethod
    async def get_device_secret(cls, device, secret_name):
        device_secrets = await SmartEMS.get_device_secrets_list(device)
        if "results" in device_secrets:
            for secret in device_secrets.get("results"):
                device_type_secret = secret.get("deviceTypeSecret")
                if device_type_secret.get("name") == secret_name:
                    return secret

        return None

    @classmethod
    async def force_renew_device_secret(cls, secret_id):
        resp = {}
        uri = (
            f"{SEMS_URL}/web/api/devicesecret/{secret_id}/enable/force/renewal"
        )
        await asyncio.gather(
            get_async(uri, resp, headers={"Authorization": f"Bearer {cls._token}"})
        )

        if resp[uri].status_code == 200:
            return resp[uri].json()
        else:
            raise SEMSError(
                f"error forcing renewal of secret <{secret_id}>",
                status_code=resp[uri].status_code,
            )

    @classmethod
    async def show_device_secret(cls, secret_id):
        resp = {}
        uri = f"{SEMS_URL}/web/api/devicesecret/{secret_id}/show"
        await asyncio.gather(
            get_async(uri, resp, headers={"Authorization": f"Bearer {cls._token}"})
        )

        if resp[uri].status_code == 200:
            return resp[uri].json()
        else:
            raise SEMSError(
                f"error showing secret <{secret_id}>", status_code=resp[uri].status_code
            )

    @classmethod
    async def get_label_by_name(cls, label_name: str):
        resp = {}
        uri = f"{SEMS_URL}/web/api/label/list"
        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json={
                    "page": 1,
                    "rowsPerPage": 10,
                    "filters": {
                        "name": {
                            "filterBy": "name",
                            "filterType": "equal",
                            "filterValue": label_name,
                        }
                    },
                },
                headers={"Authorization": f"Bearer {cls._token}"},
            )
        )
        if resp[uri].status_code != 200:
            raise SEMSError(
                f"Could not fetch label '{label_name}'",
                status_code=resp[uri].status_code,
            )
        results = resp[uri].json().get("results", [])
        if not results:
            raise SEMSError(f"Label '{label_name}' not found in SEMS", status_code=404)
        return results[0]
    
    @classmethod
    async def set_variable_for_all_devices(
        cls,
        name: str,
        value: str,
    ):
        """
        Set a variable for ALL devices using SEMS batch endpoint.
        This overwrites the variable for every device.
        """

        devices = await cls.get_device_list()
        device_ids = [d["id"] for d in devices if d.get("id")]

        if not device_ids:
            cls._log.warning("No devices found for batch variable assignment")
            return False

        uri = f"{SEMS_URL}/web/api/device/batch/variable/add"

        body = {
            "sorting": [{"field": "id", "direction": "asc"}],
            "ids": device_ids,
            "name": name,
            "variableValue": value,
        }

        resp = {}
        await asyncio.gather(
            post_async(
                uri,
                resp,
                _json=body,
                headers={"Authorization": f"Bearer {cls._token}"},
                timeout=30,
            )
        )
        
        if resp[uri].status_code in [200, 204]:
            cls._log.info(
                f"Successfully set variable '{name}' for {len(device_ids)} devices"
            )
            return True
        else:
            raise SEMSError(
                f"{SEMS_ERROR_MESSAGE}: batch variable set failed",
                status_code=resp[uri].status_code,
            )


def generate_resp_from_device_info(device_info):
    """Generate response body from device info for edit_device API calls"""
    # read current device info and copy it to second edit-device call since it sets unfilled fields to default or none

    device_serial = device_info.get("serialNumber", "unknown")

    access_tags = []
    for tag_obj in device_info.get("accessTags", []):
        access_tags.append(tag_obj["id"])

    variables = []
    for var_obj in device_info.get("variables", []):
        variables.append(
            {"name": var_obj["name"], "variableValue": var_obj["variableValue"]}
        )

    labels = []
    for label_obj in device_info.get("labels", []):
        labels.append(label_obj["id"])

    base_resp = {
        "name": device_info.get("name"),
        "labels": labels,
        "accessTags": access_tags,
        "staging": device_info.get("staging"),
        "enabled": device_info.get("enabled"),
        "variables": variables,
        "description": device_info.get("description"),
    }

    # If it is standalone edge gateway or edgeGatewayWithVpnContainerClient
    device_type = device_info.get("deviceType", {})
    communication_procedure = device_type.get("communicationProcedure")

    if communication_procedure in ["edgeGateway", "edgeGatewayWithVpnContainerClient"]:
        template_info = device_info.get("template")
        if template_info is None:
            SmartEMS._log.warning(f"Device {device_serial} has no template assigned")
            template_id = None
        else:
            template_id = template_info.get("id")

        edge_gateway_add_on = {
            "template": template_id,
            "reinstallFirmware1": device_info.get("reinstallFirmware1"),
            "reinstallConfig1": device_info.get("reinstallConfig1"),
            "requestConfigData": device_info.get("requestConfigData"),
            "endorsementKey": device_info.get("endorsementKey"),
            "registrationId": device_info.get("registrationId"),
            "hardwareVersion": device_info.get("hardwareVersion"),
            "serialNumber": device_info.get("serialNumber"),
        }
        base_resp.update(edge_gateway_add_on)

    if communication_procedure in [
        "vpnContainerClient",
        "edgeGatewayWithVpnContainerClient",
    ]:
        endpoint_devices = []
        if device_info.get("endpointDevices") is not None:
            e_access_tags = []
            for tag_obj in device_info.get("accessTags", []):
                e_access_tags.append(tag_obj["id"])
            for endpoint in device_info.get("endpointDevices"):
                endpoint_devices.append(
                    {
                        "accessTags": e_access_tags,
                        "name": endpoint.get("name"),
                        "physicalIp": endpoint.get("physicalIp"),
                        "virtualIpHostPart": endpoint.get("virtualIpHostPart"),
                    }
                )

        vpn_container_client_add_on = {
            "endpointDevices": endpoint_devices,
            "masqueradeType": device_info.get("masqueradeType"),
            "masquerades": device_info.get("masquerades"),
            "virtualSubnetCidr": device_info.get("virtualSubnetCidr"),
        }
        base_resp.update(vpn_container_client_add_on)

    return base_resp
