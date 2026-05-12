import asyncio
import json
from constants import BLOB_SAS_TOKEN_MODULE_CONF, IOT_HUB_NAME, PUBLIC_STORAGE_ACCOUNT_NAME
from async_requests import get_async
from exceptions import IoTBackendAPIError
from blob_service import BlobContainerContext
from azure.core.exceptions import ResourceNotFoundError
from helper import get_iothub_auth_headers


async def get_module_twin_config(device, module):
    """
        Returns the current configuration from the module-twin.
    """
    try:
        async with BlobContainerContext(PUBLIC_STORAGE_ACCOUNT_NAME, "iotedge-device-twin", sas_token=BLOB_SAS_TOKEN_MODULE_CONF) as container:
            blob = container.get_blob_client(f"{device}/{module}/current")
            blob_content = await blob.download_blob()
            content_str = await blob_content.readall()
            return json.loads(content_str)
    except ResourceNotFoundError:
        # If the blob twin does not exist we check whether the module exists. If the module does exist we return an empty configuration.
        # The server should not carelessly throw 404 for client requests that are absolutely justified.
        # Also the client doesn't care whether the underlying blob is there or not
        get_module_twin_url = f"https://{IOT_HUB_NAME}/twins/{device}/modules/{module}?api-version=2020-05-31-preview"
        twin_responses = {}
        headers = get_iothub_auth_headers()
        await asyncio.gather(
            get_async(get_module_twin_url, twin_responses, headers=headers, timeout=15),
        )
        if twin_responses[get_module_twin_url].status_code == 200:
            return None
        raise IoTBackendAPIError(f"Module {module} not found on device {device}", 404)
    except Exception as ex:
        raise IoTBackendAPIError(f"Could not retrieve module twin config: {str(ex)}", 500)
