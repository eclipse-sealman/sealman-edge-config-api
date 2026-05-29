import asyncio
import json
from constants import IOT_HUB_NAME
from async_requests import get_async
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers


async def get_module_twin_identity_reported(device, module):
    """
        Returns the reported section of the module twin.
    """
    try:
        get_module_identity_twin_url = f"https://{IOT_HUB_NAME}/twins/{device}/modules/{module}?api-version=2020-05-31-preview"
        twin_responses = {}
        headers = get_iothub_auth_headers()
        await asyncio.gather(
            get_async(get_module_identity_twin_url, twin_responses, headers=headers, timeout=15),
        )
        if twin_responses[get_module_identity_twin_url].status_code == 200:
            module_identity_twin = json.loads(twin_responses[get_module_identity_twin_url].text)
            reported_section = module_identity_twin.get("properties", {}).get("reported", None)
            if (reported_section is None):
                return None

            # extract only config and diagnostic(s) properties from reported section
            filtered_reported = {k: v for k, v in reported_section.items() if k in ["config", "diagnostic", "diagnostics"]}

            last_updated = reported_section.get("$metadata", {}).get("$lastUpdated", None)
            if last_updated is not None:
                filtered_reported["lastUpdated"] = last_updated

            if filtered_reported == {}:
                return None
            return filtered_reported
        elif twin_responses[get_module_identity_twin_url].status_code == 404:
            raise IoTBackendAPIError(f"Module {module} not found on device {device}", 404)
        else:
            raise IoTBackendAPIError(
                f"Could not retrieve module identity twin: {twin_responses[get_module_identity_twin_url].text}",
                twin_responses[get_module_identity_twin_url].status_code)
    except Exception as ex:
        raise IoTBackendAPIError(f"Could not retrieve module identity twin: {str(ex)}", 500)