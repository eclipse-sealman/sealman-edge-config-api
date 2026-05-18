import json
from constants import BLOB_SAS_TOKEN_TOPOLOGY, INTERNAL_STORAGE_ACCOUNT_NAME
from blob_service import BlobContainerContext
from exceptions import IoTBackendAPIError


async def get_line_by_device_id(edge_device_id):
    """
        Get the line attached to a given device
    """
    if not INTERNAL_STORAGE_ACCOUNT_NAME:
        raise IoTBackendAPIError("Internal storage account name is not set. Cannot get line.", 500)

    try:
        async with BlobContainerContext(INTERNAL_STORAGE_ACCOUNT_NAME, "topology-view", sas_token=BLOB_SAS_TOKEN_TOPOLOGY) as container:
            blob_client = container.get_blob_client(f"lines/{edge_device_id}")
            blob_data = await blob_client.download_blob(encoding="utf-8")
            blob_content = await blob_data.readall()
            return json.loads(blob_content)
    except Exception as ex:
        raise IoTBackendAPIError(f"could not retrieve data object from blob storage: {str(ex)}", 400)

