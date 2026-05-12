from exceptions import BlobError, InvalidInputError
import json
import logging
from blob_service import BlobContainerContext

from constants import (
    DEFAULT_SEMS_TEMPLATE_BLOB_CONTAINER_NAME,
    DEFAULT_SEMS_TEMPLATE_BLOB_FILE_NAME,
    INTERNAL_STORAGE_ACCOUNT_NAME,
    BLOB_SAS_TOKEN_SEMS_TEMPLATE,
)

logger = logging.getLogger("EdgeConfigAPI")

class DeviceTypeConfigRepo:

    @classmethod
    async def get_default_config(cls) -> dict:
        if not INTERNAL_STORAGE_ACCOUNT_NAME:
            raise InvalidInputError(
                "Internal storage account name is not set. Cannot retrieve default configuration.",
                status_code=500
            )
        if not DEFAULT_SEMS_TEMPLATE_BLOB_CONTAINER_NAME:
            raise InvalidInputError(
                "DEFAULT_SEMS_TEMPLATE_BLOB_CONTAINER_NAME is not properly configured.",
                status_code=500
            )
        if not DEFAULT_SEMS_TEMPLATE_BLOB_FILE_NAME:
            raise InvalidInputError(
                "DEFAULT_SEMS_TEMPLATE_BLOB_FILE_NAME is not properly configured.",
                status_code=500
            )

        try:
            async with BlobContainerContext(
                INTERNAL_STORAGE_ACCOUNT_NAME,
                DEFAULT_SEMS_TEMPLATE_BLOB_CONTAINER_NAME,
                sas_token=BLOB_SAS_TOKEN_SEMS_TEMPLATE
            ) as container:
                blob_client = container.get_blob_client(DEFAULT_SEMS_TEMPLATE_BLOB_FILE_NAME)
                blob_downloader = await blob_client.download_blob()
                blob_data = await blob_downloader.readall()
                blob_content_as_string = blob_data.decode("utf-8")

                return json.loads(blob_content_as_string)

        except Exception as e:
            raise BlobError(
                f"Error while retrieving default configuration template. Error details: {e}",
                400,
            ) from e