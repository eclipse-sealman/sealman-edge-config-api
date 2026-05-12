import asyncio
import uuid
from constants import IOT_HUB_NAME, BLOB_SAS_TOKEN_MODULE_CONF, PUBLIC_STORAGE_ACCOUNT_NAME
from async_requests import patch_async
from exceptions import IoTBackendAPIError
from exceptions import UploadError
from helper import get_iothub_auth_headers
from blob_service import BlobContainerContext


async def post_module_twin_config(device, module, request):
    """
        Post a configuration to the module twin and triggers a twin-change event.
    """

    # Validate SAS token is available - required for Edge Device to access the blob
    if not PUBLIC_STORAGE_ACCOUNT_NAME:
        raise IoTBackendAPIError("Public storage account name is not set. Cannot upload file.", 500)

    if not BLOB_SAS_TOKEN_MODULE_CONF or BLOB_SAS_TOKEN_MODULE_CONF.strip() == "":
        raise IoTBackendAPIError("BLOB_SAS_TOKEN_MODULE_CONF is not configured. SAS token is required for Edge Device blob access.", 500)

    try:
        async with BlobContainerContext(PUBLIC_STORAGE_ACCOUNT_NAME, "iotedge-device-twin", sas_token=BLOB_SAS_TOKEN_MODULE_CONF) as container:
            blob = container.get_blob_client(f"{device}/{module}/current")
            await blob.upload_blob(request.model_dump_json(exclude_none=True), overwrite=True)
    except Exception as ex:
        raise UploadError(f"could not upload data object to blob storage: {str(ex)}", 400)

    config_id = uuid.uuid4().hex
    patch = {
            "properties": {
                "desired": {
                    "configBlobUrl": f"https://{PUBLIC_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/iotedge-device-twin/{device}/{module}/current?{BLOB_SAS_TOKEN_MODULE_CONF}",
                    "configId": config_id
                }
            }
        }

    responses = {}
    url2 = f"https://{IOT_HUB_NAME}/twins/{device}/modules/{module}?api-version=2020-05-31-preview"
    headers = get_iothub_auth_headers()
    await asyncio.gather(
            patch_async(url2, responses, _json=patch, headers=headers)
    )

    if responses[url2].status_code == 200:
        return request, 200
    else:
        raise IoTBackendAPIError(f"could not update module twin of {module} on {device} on the Azure IoT Hub. "
                                     f"Config-Id: [{config_id}]. Error: {str(responses[url2].text)}",
                                     responses[url2].status_code)
