"""Core routes on the internal side app that let an extension read Core data.

They live under `/core/extensions/{name}` so they cannot collide with an extension's
own `internal` routes. Auth is the extension's `X-Internal-Key` (same check as the
extension `internal` routes); the extension must also be enabled.

Must not import `extensions.setup` (it includes `core_router`), to avoid an import cycle.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from db.repos.endpoint import EndpointRepository
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from helper import AuditTrail

from .runtime import verify_internal_key
from .schemas import InternalEndpoint, InternalEndpointPage

core_router = APIRouter(prefix="/core/extensions/{name}")


async def core_extension_caller(
    name: str,
    request: Request,
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
    extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
) -> Dict[str, Any]:
    """Authenticates the calling extension and returns its row. 401 for a missing or
    wrong key (including another extension's key), 403 for a disabled extension."""
    row = await verify_internal_key(request, name, x_internal_key, extension_repo)
    if not row["enabled"]:
        access = f"internal {request.method} {request.url.path}"
        await AuditTrail.log(f"extension:{name}", f"{access} rejected: extension disabled")
        raise HTTPException(status_code=403, detail=f"Extension '{name}' is disabled")
    return row


def managed_keys_by_type(types: List[Dict[str, Any]], extension: str) -> Dict[str, List[str]]:
    """{type_id: sorted keys managed by `extension`}, only for types with at least one such key."""
    result: Dict[str, List[str]] = {}
    for endpoint_type in types:
        keys = sorted(
            key for key, owner in (endpoint_type.get("managed_by") or {}).items() if owner == extension
        )
        if keys:
            result[endpoint_type["type_id"]] = keys
    return result


def project_endpoint(row: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    """The `InternalEndpoint` shape of a raw endpoint row, with only the stored values of `keys`."""
    stored = row["endpoint_data"]
    return {
        "endpoint_id": row["endpoint_id"],
        "device_id": row["device_id"],
        "type_id": row["type_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "data": {key: stored[key] for key in keys if key in stored},
    }


@core_router.get("/devices/{device_id}/endpoints", response_model=List[InternalEndpoint])
async def get_device_endpoints(
    name: str,
    device_id: str,
    caller: Dict[str, Any] = Depends(core_extension_caller),
    endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
) -> List[Dict[str, Any]]:
    """The device's endpoints whose type has at least one field managed by this extension, with
    only those fields' stored values. Ordered by `created_at`, then `endpoint_id`."""
    keys_by_type = managed_keys_by_type(await endpoint_repo.get_endpoint_types(), name)
    rows = await endpoint_repo.get_endpoints_by_types(device_id, list(keys_by_type))
    return [project_endpoint(row, keys_by_type[row["type_id"]]) for row in rows]


@core_router.get("/endpoints", response_model=InternalEndpointPage)
async def get_endpoints_across_devices(
    name: str,
    non_empty: Optional[str] = None,
    page: int = Query(1, ge=1, le=1_000_000),
    page_size: int = Query(20, ge=1, le=100),
    caller: Dict[str, Any] = Depends(core_extension_caller),
    endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
) -> Dict[str, Any]:
    """Devices with at least one in-scope endpoint (with `non_empty`: whose stored value for that
    key, managed by this extension, is not missing, null or ""), paginated by device. Devices are
    ordered by their earliest matching endpoint's `created_at` descending, then `device_id`; each
    lists only its matching endpoints, by `created_at`, then `endpoint_id`."""
    if non_empty is not None and non_empty not in (caller.get("endpoint_fields") or {}):
        raise HTTPException(
            status_code=422,
            detail=f"'{non_empty}' is not an endpoint field of extension '{name}'",
        )
    keys_by_type = managed_keys_by_type(await endpoint_repo.get_endpoint_types(), name)
    if non_empty is not None:
        keys_by_type = {t: keys for t, keys in keys_by_type.items() if non_empty in keys}
    rows, total_devices = await endpoint_repo.page_devices_by_types(
        list(keys_by_type), non_empty, page, page_size
    )
    devices: List[Dict[str, Any]] = []
    for row in rows:
        if not devices or devices[-1]["device_id"] != row["device_id"]:
            devices.append({"device_id": row["device_id"], "endpoints": []})
        devices[-1]["endpoints"].append(project_endpoint(row, keys_by_type[row["type_id"]]))
    return {
        "devices": devices,
        "page": page,
        "page_size": page_size,
        "total_devices": total_devices,
    }
