import asyncio
import logging
from constants import IOT_HUB_NAME
from async_requests import get_async
from exceptions import IoTBackendAPIError
from helper import get_iothub_auth_headers

logger = logging.getLogger(__name__)

# Can this help us to get the list of deployments on the IoT Hub?
async def get_module_deployment_status(device_id: str):
    """
        Returns the deployment status of the iotedge module deployment
    """
    resp = {}
    url_get_config = f"https://{IOT_HUB_NAME}/configurations?api-version=2020-05-31-preview"
    url_get_device_tags = f"https://{IOT_HUB_NAME}/twins/{device_id}?api-version=2021-04-12"
    url_get_deployment_applied = (f"https://{IOT_HUB_NAME}/twins/{device_id}/modules/$edgeAgent"
                                  f"?api-version=2021-04-12")
    headers = get_iothub_auth_headers()
    await asyncio.gather(
        get_async(url_get_config, resp, headers=headers),
        get_async(url_get_device_tags, resp, headers=headers)
    )

    if resp[url_get_config].status_code == 200 and resp[url_get_device_tags].status_code == 200:
        configurations = resp[url_get_config].json()
        device_tags = resp[url_get_device_tags].json()
        
        # Safely access device tags
        tags = device_tags.get("tags", {})
        deployment_tag = tags.get("deployment")
        
        if deployment_tag is None:
            # If there is no deployment tag, return default status
            return {
                "deviceId": device_id,
            }

        desired_deployment_id = None
        current_prio = 0
        targeted = False
        for config in configurations:
            if config["targetCondition"] == f"tags.deployment='{deployment_tag}'":
                targeted = True
                if config["priority"] > current_prio:
                    current_prio = config["priority"]
                    desired_deployment_id = config["id"]

        applied = False
        success = False

        await asyncio.gather(
            get_async(url_get_deployment_applied, resp, headers=headers)
        )

        if resp[url_get_deployment_applied].status_code == 200:
            edge_agent_obj = resp[url_get_deployment_applied].json()
            
            # Log the structure for debugging
            logger.debug(f"EdgeAgent response keys: {list(edge_agent_obj.keys())}")
            
            # Check if configurations key exists in the response
            # Fix for KeyError: 'configurations' - the key might not exist in all responses
            configurations_data = edge_agent_obj.get("configurations", {})
            deployment = configurations_data.get(desired_deployment_id) if desired_deployment_id else None
            
            if deployment is None:
                logger.debug(f"No deployment found for ID: {desired_deployment_id}")
                applied = False
            else:
                if deployment.get("status") == "Applied":
                    applied = True
                else:
                    applied = False
            
            # Safely access nested properties with error handling
            properties = edge_agent_obj.get("properties", {})
            desired_props = properties.get("desired", {})
            reported_props = properties.get("reported", {})
            
            desired_version = desired_props.get("$version")
            last_desired_version = reported_props.get("lastDesiredVersion")
            last_desired_status = reported_props.get("lastDesiredStatus", {})
            last_desired_status_code = last_desired_status.get("code")
            
            if (
                applied
                and desired_version is not None
                and last_desired_version is not None
                and desired_version == last_desired_version
                and last_desired_status_code is not None
            ):
                success = True

            return {
                "deviceId": device_id,
                "deploymentId": desired_deployment_id,
                "priority": current_prio,
                "targeted": targeted,
                "applied": applied,
                "success": success
            }
        else:
            raise IoTBackendAPIError(f"cannot read deployment status from edgeAgent twin of: {device_id}", 400)
