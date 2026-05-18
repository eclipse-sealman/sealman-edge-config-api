from constants import BLOB_SAS_TOKEN_TOPOLOGY, INTERNAL_STORAGE_ACCOUNT_NAME
from blob_service import BlobContainerContext
from exceptions import UploadError


async def update_line_by_device_id(edge_device_id, line):
    """
        Update the line for a given device
    """
    if not INTERNAL_STORAGE_ACCOUNT_NAME:
        raise UploadError("Internal storage account name is not set. Cannot update line.", 500)

    try:
        async with BlobContainerContext(INTERNAL_STORAGE_ACCOUNT_NAME, "topology-view", sas_token=BLOB_SAS_TOKEN_TOPOLOGY) as container:
            blob_client = container.get_blob_client(f"lines/{edge_device_id}")
            await blob_client.upload_blob(line.model_dump_json(exclude_none=True), overwrite=True)
    except Exception as ex:
        raise UploadError(f"could not upload data object to blob storage: {str(ex)}", 400)

    return line

