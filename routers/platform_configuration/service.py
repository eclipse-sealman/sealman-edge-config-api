import json
import logging
import secrets
from typing import Any, Dict, List
from constants import SEMS_URL, BOOTSTRAP_SEMS_TEMPLATE_NAME, AZURE_IOT_EDGE_ENABLED_VARIABLE
from exceptions import APIError
from smart_ems import SmartEMS
from async_requests import post_async

logger = logging.getLogger("EdgeConfigAPI")


EDGE_ALLOWED_TYPES = [
    "Edge gateway",
    "Edge gateway with VPN Container Client"
]


async def get_available_templates(selected_names: List[str]):
    try:
        if not SmartEMS.init_done():
            raise APIError("SmartEMS not initialized", 500)

        resp = {}
        uri = f"{SEMS_URL}/web/api/template/list"

        await post_async(
            uri,
            resp,
            _json={
                "page": 1,
                "rowsPerPage": 200,
                "sorting": [],
                "filters": {},
            },
            headers={"Authorization": f"Bearer {SmartEMS._token}"},
        )

        if resp[uri].status_code != 200:
            raise APIError(f"SEMS template fetch failed: {resp[uri].status_code}", 500)

        results = resp[uri].json().get("results", [])
        selected_set = set(selected_names)

        templates = []
        for template in results:
            device_type = template.get("deviceType", {}).get("name")
            production_name = template.get("representation") or template.get("name")

            if device_type in EDGE_ALLOWED_TYPES and production_name:
                templates.append({
                    "id": template.get("id"),
                    "name": template.get("name") or template.get("representation"),
                    "selected": production_name in selected_set,
                })

        return templates

    except APIError:
        raise
    except Exception as ex:
        raise APIError(f"Failed to fetch templates from SEMS: {str(ex)}", 500)


async def test_device_config(template_variables: Dict[str, str], azure_iot_edge_enabled: bool = True) -> Dict[str, Any]:
    """Render the bootstrap config for a throwaway SEMS device using the given platform variables."""
    serial = f"99{secrets.randbelow(10**10):010d}"
    variables = {k: str(v) for k, v in template_variables.items() if v}
    variables["azure_provisioning"] = "dps"
    variables[AZURE_IOT_EDGE_ENABLED_VARIABLE] = "true" if azure_iot_edge_enabled else "false"
    sems_device_id = None

    try:
        template = await SmartEMS.get_template_by_template_name(BOOTSTRAP_SEMS_TEMPLATE_NAME)
        await SmartEMS.create_device({
            "name": serial,
            "serialNumber": serial,
            "registrationId": serial,
            "description": "Temporary device for platform config test",
            "deviceType": str(template.get("deviceType", {}).get("id", 9)),
            "variables": [{"name": n, "variableValue": v} for n, v in variables.items()],
            "enabled": True,
            "staging": False,
            "template": template.get("id"),
        })
        sems_device = await SmartEMS.get_device_by_serial(serial, require_template=False)
        sems_device_id = sems_device["id"]
        await SmartEMS.device_config_download(sems_device_id)
        return {"valid": True, "message": "The device configuration could be generated successfully."}
    except Exception as ex:
        return {"valid": False, "message": str(ex)}
    finally:
        try:
            if sems_device_id is None:
                leftover = await SmartEMS.get_device_by_serial(serial, require_template=False)
                sems_device_id = leftover["id"] if leftover else None
            if sems_device_id is not None:
                await SmartEMS.delete_device(sems_device_id)
        except Exception as cleanup_error:
            logger.warning(f"Could not delete test device {serial} from SEMS: {cleanup_error}")