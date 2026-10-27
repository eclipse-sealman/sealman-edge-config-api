import re
from typing import Any, Callable, Dict, List

from starlette.datastructures import QueryParams

from db.repos.device import DeviceRepository
from exceptions import APIError

_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")
_DEVICE_DATA_PREFIX = "device_data."
_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 1000


def _fields(query_params: QueryParams, name: str) -> List[str]:
    keys = query_params.getlist(name)
    for key in keys:
        if not _KEY_RE.fullmatch(key):
            raise APIError(f"Invalid {name} '{key}'", 400)
    return list(dict.fromkeys(keys))


def _int_param(query_params: QueryParams, name: str, default: int, minimum: int, maximum: int | None) -> int:
    raw = query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise APIError(f"{name} must be an integer", 400)
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise APIError(f"{name} must be {bounds}", 400)
    return value


def _order_by(query_params: QueryParams) -> str:
    order_by = query_params.get("order_by", "created_at")
    if order_by == "created_at":
        return order_by
    if order_by.startswith(_DEVICE_DATA_PREFIX) and _KEY_RE.fullmatch(order_by[len(_DEVICE_DATA_PREFIX):]):
        return order_by
    raise APIError("order_by must be 'created_at' or 'device_data.<key>'", 400)


def _descending(query_params: QueryParams) -> bool:
    order = query_params.get("order", "desc")
    if order not in ("asc", "desc"):
        raise APIError("order must be 'asc' or 'desc'", 400)
    return order == "desc"


async def get_devices_with_data(
    repo: DeviceRepository,
    query_params: QueryParams,
    is_unrestricted: bool,
    filter_device: Callable[[Dict[str, Any]], bool],
) -> Dict[str, Any]:
    """Devices (and their endpoints) holding a non-null value for any requested field.

    `is_unrestricted` must be True only when the caller is unrestricted for every required
    permission; `filter_device` must accept a device only when it is in scope for all of them.
    """
    device_fields = _fields(query_params, "device_field")
    endpoint_fields = _fields(query_params, "endpoint_field")
    if not device_fields and not endpoint_fields:
        raise APIError("At least one device_field or endpoint_field is required", 400)
    order_by = _order_by(query_params)
    descending = _descending(query_params)
    page = _int_param(query_params, "page", 1, 1, None)
    page_size = _int_param(query_params, "page_size", _DEFAULT_PAGE_SIZE, 1, _MAX_PAGE_SIZE)
    offset = (page - 1) * page_size

    if is_unrestricted:
        total = await repo.count_devices_with_data(device_fields, endpoint_fields)
        page_devices = await repo.get_devices_with_data(
            device_fields, endpoint_fields, order_by, descending, limit=page_size, offset=offset
        )
    else:
        # By design, scoped callers load every match before paging (scope filtering is
        # in Python); the export service account is unscoped and pages in SQL.
        matches = await repo.get_devices_with_data(device_fields, endpoint_fields, order_by, descending)
        visible = [device for device in matches if filter_device(device["device_data"])]
        total = len(visible)
        page_devices = visible[offset:offset + page_size]

    endpoints = await repo.get_endpoints_with_fields(
        [device["device_id"] for device in page_devices], endpoint_fields
    )
    return {
        "total": total,
        "items": [
            {
                "device_id": device["device_id"],
                "type_id": device["type_id"],
                "device_data": device["device_data"],
                "created_at": device["created_at"],
                "endpoints": endpoints.get(device["device_id"], []),
            }
            for device in page_devices
        ],
    }
