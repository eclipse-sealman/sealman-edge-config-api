import json
import re
from urllib.parse import unquote, urlsplit
from constants import (
    BLOB_SAS_TOKEN_MODULE_CONF, IOT_HUB_NAME, PUBLIC_STORAGE_ACCOUNT_NAME,
    MODULE_CONFIG_STORAGE_URL, MODULE_CONFIG_DEVICE_STORAGE_URL,
)
from async_requests import get_async
from exceptions import IoTBackendAPIError
from blob_service import BlobContainerContext, blob_url
from azure.core.exceptions import ResourceNotFoundError
from helper import encode_iothub_id, get_iothub_auth_headers
from routers.module_config.secrets import mask_config


def blob_name_from_desired(device, module, desired):
    """
        Returns the blob name (`current` or the `configId`) the desired properties point to,
        rejecting pointers outside `{device}/{module}/` of the configured storage.
    """
    pointer = desired.get("configBlobUrl")
    filename = "current"
    if pointer:
        expected = urlsplit(blob_url(
            PUBLIC_STORAGE_ACCOUNT_NAME, "iotedge-device-twin", f"{device}/{module}/", "",
            account_url=MODULE_CONFIG_DEVICE_STORAGE_URL or MODULE_CONFIG_STORAGE_URL,
        ))
        actual = urlsplit(pointer)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or not actual.path.startswith(expected.path):
            raise IoTBackendAPIError("Module configuration points outside the configured storage location", 409)
        filename = unquote(actual.path[len(expected.path):])
        if filename != "current" and (
            not re.fullmatch(r"[a-f0-9]{32}", filename) or filename != desired.get("configId")
        ):
            raise IoTBackendAPIError("Module configuration has an unsupported blob reference", 409)
    return filename


async def get_module_twin_desired(device, module):
    url = f"https://{IOT_HUB_NAME}/twins/{encode_iothub_id(device)}/modules/{encode_iothub_id(module)}?api-version=2020-05-31-preview"
    responses = {}
    await get_async(url, responses, headers=get_iothub_auth_headers(), timeout=15)
    response = responses[url]
    if response.status_code != 200:
        raise IoTBackendAPIError(f"Could not read module {module} on device {device}", response.status_code)
    return response.json().get("properties", {}).get("desired", {})


async def get_module_twin_config(device, module, repo=None):
    """
        Returns the current configuration from the module-twin. With the revision repository, the
        secret paths of the revision of the current `configId` are masked.
    """
    desired = await get_module_twin_desired(device, module)
    pointer = desired.get("configBlobUrl")
    filename = blob_name_from_desired(device, module, desired)
    content = await read_module_config_blob(device, module, filename, missing_ok=not pointer)
    config_id = desired.get("configId")
    if repo is None or content is None or not config_id:
        return content
    revision = await repo.get_revision_by_config_id(device, module, str(config_id))
    secret_paths = revision.get("secret_paths") if revision else None
    return mask_config(content, secret_paths) if secret_paths else content


async def read_module_config_blob(device, module, filename, missing_ok=False):
    """
        Reads and parses the config blob `{device}/{module}/{filename}`.
        Returns None for a missing blob if `missing_ok`.
    """
    try:
        async with BlobContainerContext(
            PUBLIC_STORAGE_ACCOUNT_NAME, "iotedge-device-twin", sas_token=BLOB_SAS_TOKEN_MODULE_CONF,
            account_url=MODULE_CONFIG_STORAGE_URL,
        ) as container:
            blob = container.get_blob_client(f"{device}/{module}/{filename}")
            blob_content = await blob.download_blob()
            content_str = await blob_content.readall()
            return json.loads(content_str)
    except ResourceNotFoundError:
        if missing_ok:
            return None
        raise IoTBackendAPIError("The module's referenced configuration blob is missing", 404)
    except Exception as ex:
        raise IoTBackendAPIError(f"Could not retrieve module twin config: {str(ex)}", 500)
