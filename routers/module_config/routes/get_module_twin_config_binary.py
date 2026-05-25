import base64

from constants import BLOB_SAS_TOKEN_MODULE_CONF_INTERNAL, INTERNAL_STORAGE_ACCOUNT_NAME
from exceptions import IoTBackendAPIError
from blob_service import BlobContainerContext


async def get_module_twin_config_binary(device: str, module: str):
    """
    Returns exactly one module config file from blob storage as base64 payload.
    """
    prefix = f"{device}/{module}/"

    try:
        async with BlobContainerContext(
            INTERNAL_STORAGE_ACCOUNT_NAME,
            "iotedge-device-twin",
            sas_token=BLOB_SAS_TOKEN_MODULE_CONF_INTERNAL,
        ) as container:
            blob_names: list[str] = []
            async for blob in container.list_blobs(name_starts_with=prefix):
                if blob.name and not blob.name.endswith("/"):
                    blob_names.append(blob.name)

            if len(blob_names) != 1:
                raise IoTBackendAPIError(
                    f"Expected exactly one file, found {len(blob_names)}.",
                    400,
                )

            blob_name = blob_names[0]
            blob_client = container.get_blob_client(blob_name)
            blob_downloader = await blob_client.download_blob()
            blob_data = await blob_downloader.readall()
            blob_properties = await blob_client.get_blob_properties()

            content_type = "application/octet-stream"
            if blob_properties and blob_properties.content_settings and blob_properties.content_settings.content_type:
                content_type = blob_properties.content_settings.content_type

            return {
                "filename": blob_name.split("/")[-1],
                "contentType": content_type,
                "encoding": "base64",
                "data": base64.b64encode(blob_data).decode("ascii"),
            }
    except IoTBackendAPIError:
        raise
    except Exception as ex:
        raise IoTBackendAPIError(f"Could not retrieve module twin binary config: {str(ex)}", 500)

