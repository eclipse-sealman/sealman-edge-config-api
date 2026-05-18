from constants import BLOB_SAS_TOKEN_TOPOLOGY, INTERNAL_STORAGE_ACCOUNT_NAME
from exceptions import IoTBackendAPIError
from blob_service import BlobContainerContext


async def delete_line_by_device_id(edge_device_id):
    """
        Delete the line for a given device
    """
    if not INTERNAL_STORAGE_ACCOUNT_NAME:
        raise IoTBackendAPIError("Internal storage account name is not set. Cannot delete line.", 500)

    try:
        async with BlobContainerContext(INTERNAL_STORAGE_ACCOUNT_NAME, "topology-view", sas_token=BLOB_SAS_TOKEN_TOPOLOGY) as container:
            blob_client = container.get_blob_client(f"lines/{edge_device_id}")
            await blob_client.delete_blob()
    except Exception as ex:
        raise IoTBackendAPIError(f"could not delete data object from blob storage: {str(ex)}", 400)

