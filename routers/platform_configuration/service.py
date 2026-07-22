import json
from typing import Any, List
from constants import SEMS_URL
from exceptions import APIError
from smart_ems import SmartEMS
from async_requests import post_async


EDGE_ALLOWED_TYPES = [
    "Edge gateway",
    "Edge gateway with VPN Container Client"
]


async def get_available_templates(selected_names: List[str]):
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
                "filters": {},
            },
            headers={"Authorization": f"Bearer {SmartEMS._token}"},
        )

        if resp[uri].status_code != 200:
            raise APIError(f"SEMS template fetch failed: {resp[uri].status_code}", 500)

        results = resp[uri].json().get("results", [])
        selected_set = set(selected_names)

        templates = []
        for template in results:
            device_type = template.get("deviceType", {}).get("name")
            production_name = template.get("representation") or template.get("name")

            if device_type in EDGE_ALLOWED_TYPES and production_name:
                templates.append({
                    "id": template.get("id"),
                    "name": template.get("name") or template.get("representation"),
                    "selected": production_name in selected_set,
                })

        return templates

    except APIError:
        raise
    except Exception as ex:
        raise APIError(f"Failed to fetch templates from SEMS: {str(ex)}", 500)