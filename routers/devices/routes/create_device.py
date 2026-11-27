from async_requests import get_async, put_async, delete_async
import logging
from smart_ems import SmartEMS
from exceptions import APIError, IoTBackendAPIError
from db.repos.device import DeviceRepository
from db.repos.device_template import DeviceTemplateRepository
from db.repos.platform_functions import PlatformFunctionsRepository
import asyncio
from smart_ems import generate_resp_from_device_info
from routers.devices.routes.get_devices import populate_cache_from_iot_hub_query
from constants import IOT_HUB_NAME, BOOTSTRAP_SEMS_TEMPLATE_NAME, AZURE_IOT_EDGE_ENABLED_VARIABLE
from helper import encode_iothub_id, get_iothub_auth_headers
from routers.platform_configuration.readiness import missing_required_variables

logger = logging.getLogger("EdgeConfigAPI")

IOT_HUB_API_VERSION = "2021-04-12"

async def create_device(
    device_id: str,
    body: dict,
    repo: DeviceRepository,
    template_repo: DeviceTemplateRepository,
    functions_repo: PlatformFunctionsRepository,
):
    encoded_device = encode_iothub_id(device_id)
    auth_type = body.get("authType")
    is_dps = auth_type == "tpm"
    metadata = body.get("meta", {})
    type_id = body.get("type_id") or "default"
    # Only TPM/DPS devices may carry a registration ID.
    registration_id = (body.get("registrationId") or "").strip() if is_dps else ""

    # 1. PRE-CHECK
    missing = missing_required_variables(
        await template_repo.get_template_variables(),
        (await functions_repo.get_functions()).azure_iot_edge_enabled,
    )
    if missing:
        raise APIError(
            f"The platform is not fully configured. Missing device settings: {', '.join(missing)}", 400
        )

    if await repo.device_exists(device_id):
        raise APIError(f"Device {device_id} already exists", 409)

    sems_device = await SmartEMS.get_device_by_serial(device_id, require_template=False)
    if is_dps and not registration_id and not (sems_device and sems_device.get("registrationId")):
        raise APIError(
            "No registration ID entered and device is not already provisioned", 400
        )

    created_in_db = False
    created_in_iothub = False
    headers = {}

    try:
        # 2. INSERT DB
        device = await repo.create_device(device_id, metadata, type_id=type_id)
        created_in_db = True

        # 3. REGISTER IN IoTHub
        url = f"https://{IOT_HUB_NAME}/devices/{encoded_device}?api-version={IOT_HUB_API_VERSION}"
        headers = get_iothub_auth_headers()

        body_req = {
            "deviceId": device_id,
            "authentication": {
                "type": "sas",
                "symmetricKey": {"primaryKey": None, "secondaryKey": None}
            },
            "capabilities": {
                "iotEdge": True
            }
        }
        responses = {}
        await asyncio.gather(put_async(url, responses, _json=body_req, headers=headers))
        resp = responses[url]

        if resp.status_code not in [200, 201, 409]:
            raise IoTBackendAPIError(resp.text, resp.status_code)
        created_in_iothub = True

        # 4. GET CONNECTION STRING FROM IoTHub (manual provisioning only)
        connection_string = None
        if not is_dps:
            responses = {}
            await asyncio.gather(get_async(url, responses, headers=headers))
            resp = responses[url]

            if resp.status_code != 200:
                raise IoTBackendAPIError(resp.text, resp.status_code)

            iothub_device = resp.json()
            primary_key = iothub_device["authentication"]["symmetricKey"]["primaryKey"]
            connection_string = f"HostName={IOT_HUB_NAME};DeviceId={device_id};SharedAccessKey={primary_key}"

        # 5. REGISTER IN SEMS
        # Start with infrastructure constants from env.
        variables_to_set = dict(await template_repo.get_template_variables())
        # Add per-device variables (user input + derived).
        # The template renders the azure block only for "manual" or "dps".
        variables_to_set["azure_provisioning"] = "dps" if is_dps else "manual"
        # The IoT Hub device is created either way; this only controls whether the azure block is rendered.
        azure_enabled = (await functions_repo.get_functions()).azure_iot_edge_enabled
        variables_to_set[AZURE_IOT_EDGE_ENABLED_VARIABLE] = "true" if azure_enabled else "false"
        if is_dps:
            variables_to_set.pop("device_connection_string", None)
        else:
            variables_to_set["device_connection_string"] = connection_string

        # Remove empty values before sending to SEMS.
        variables_to_set = {k: str(v) for k, v in variables_to_set.items() if v}

        template = None
        template_id = None
        try:
            template = await SmartEMS.get_template_by_template_name(BOOTSTRAP_SEMS_TEMPLATE_NAME)
            template_id = template.get("id")
            logger.info(f"Found template '{BOOTSTRAP_SEMS_TEMPLATE_NAME}' with id: {template_id}")
        except Exception as e:
            logger.error(f"Could not fetch template '{BOOTSTRAP_SEMS_TEMPLATE_NAME}': {e}")
            # template_id = None

        if sems_device:
            clean_body = generate_resp_from_device_info(sems_device)
            variables = clean_body.get("variables", [])

            existing_value = None
            for var in variables:
                if var["name"] == "device_connection_string":
                    existing_value = var.get("variableValue")
                    break

            if not is_dps and existing_value and existing_value != connection_string:
                raise APIError("SEMS device already has different connection string", 400)

            # Merge required values into existing variable list by name.
            merged_variables = {var.get("name"): var.get("variableValue") for var in variables if var.get("name")}
            merged_variables.update(variables_to_set)
            clean_body["variables"] = [
                {"name": name, "variableValue": value}
                for name, value in merged_variables.items()
            ]
            if registration_id:
                clean_body["registrationId"] = registration_id
            await SmartEMS.edit_device(sems_device["id"], clean_body)

        else:
            payload = {
                "name": device_id,
                "serialNumber": device_id,
                "imei": body.get("imei"),
                "model": body.get("model"),
                "registrationId": registration_id or None,
                "endorsementKey": body.get("endorsementKey"),
                "hardwareVersion": body.get("hardwareVersion"),
                "description": body.get("description", ""),
                "deviceType": str(template.get("deviceType", {}).get("id", 9)) if template else body.get("deviceType", "9"),
                "variables": [
                    {"name": name, "variableValue": value}
                    for name, value in variables_to_set.items()
                ],
                "enabled": True,
                "staging": False,
                "template": template_id,
            }
            await SmartEMS.create_device(payload)

        # 6. SUCCESS
        await populate_cache_from_iot_hub_query(repo)
        return device

    except Exception as e:
        if created_in_iothub:
            try:
                del_url = f"https://{IOT_HUB_NAME}/devices/{encoded_device}?api-version={IOT_HUB_API_VERSION}"
                responses = {}
                await asyncio.gather(delete_async(del_url, responses, headers=headers))
            except:
                pass
        if created_in_db:
            await repo.delete_device(device_id)
        raise e