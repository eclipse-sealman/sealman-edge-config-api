import json
from typing import Any
from blob_service import BlobContainerContext

from constants import (
    INTERNAL_STORAGE_ACCOUNT_NAME,
    BLOB_SAS_TOKEN_PLATFORM_CONFIG,
    SEMS_URL
)

from exceptions import APIError
from smart_ems import SmartEMS
from async_requests import post_async


PLATFORM_CONTAINER = "platform-config"

TEMPLATES_FILE = "templates.json"
ENDPOINT_TYPES_FILE = "endpoint-types.json"
SERVICES_FILE = "services.json"


EDGE_ALLOWED_TYPES = [
    "Edge gateway",
    "Edge gateway with VPN Container Client"
]



# -------------------- Blob Read --------------------

async def read_json_blob(container: str, blob_name: str):
    try:
        async with BlobContainerContext(
            INTERNAL_STORAGE_ACCOUNT_NAME,
            container,
            sas_token=BLOB_SAS_TOKEN_PLATFORM_CONFIG or None
        ) as container_client:
            blob = container_client.get_blob_client(blob_name)
            downloader = await blob.download_blob(encoding="utf-8")
            data = await downloader.readall()

            return json.loads(data)

    except Exception as e:
        raise APIError(f"Failed to read {blob_name}: {str(e)}", 500)


# -------------------- Blob Write --------------------

async def write_json_blob(container: str, blob_name: str, payload: Any):
    try:
        async with BlobContainerContext(
            INTERNAL_STORAGE_ACCOUNT_NAME,
            container,
            sas_token=BLOB_SAS_TOKEN_PLATFORM_CONFIG or None
        ) as container_client:

            blob = container_client.get_blob_client(blob_name)

            await blob.upload_blob(
                json.dumps(payload, indent=2),
                overwrite=True,
                content_type="application/json"
            )

    except Exception as e:
        raise APIError(f"Failed to write {blob_name}: {str(e)}", 500)

# -------------------- SEMS Templates --------------------

async def get_available_templates():
    try:
        if not SmartEMS.init_done():
            raise APIError("SmartEMS not initialized", 500)

        resp = {}

        uri = f"{SEMS_URL}/web/api/template/list"

        await post_async(
            uri,
            resp,
            _json={
                "page": 1,
                "rowsPerPage": 200,
                "sorting": [],
                "filters": {}
            },
            headers={
                "Authorization": f"Bearer {SmartEMS._token}"
            }
        )

        if resp[uri].status_code != 200:
            raise APIError(
                f"SEMS template fetch failed: {resp[uri].status_code}", 500
            )

        results = resp[uri].json().get("results", [])

        selected_blob = await read_json_blob(
            PLATFORM_CONTAINER,
            TEMPLATES_FILE
        )

        selected_names = set(selected_blob.get("selected", []))


        templates = []

        for template in results:
           device_type = template.get("deviceType", {}).get("name")
           production_name = template.get("representation") or template.get("name")

           if device_type in EDGE_ALLOWED_TYPES and production_name:
               templates.append({
                     "id": template.get("id"),
                     "name": template.get("name") or template.get("representation"),
                     "selected": production_name  in selected_names
               })
        return templates

    except Exception as ex:
        raise APIError(f"Failed to fetch templates from SEMS: {str(ex)}", 500)
