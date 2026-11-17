from typing import Optional

from authorization.abac_permission_check import ABACDeviceListFilterResult
from db.repos.device import DeviceRepository
from db.repos.device_search import DeviceSearchRepository, SearchKind
from exceptions import APIError
from routers.devices.routes.search_devices import allowed_device_ids, needs_endpoint_permission
from routers.devices.schemas import DeviceSearchValuesResponse

SUGGESTION_LIMIT = 10
COLUMN_VALUES_LIMIT = 100


async def get_search_values(
    repo: DeviceSearchRepository,
    device_repo: DeviceRepository,
    kind: SearchKind,
    field: Optional[str],
    query: Optional[str],
    limit: int,
    device_read: ABACDeviceListFilterResult,
    endpoint_read: Optional[ABACDeviceListFilterResult],
) -> DeviceSearchValuesResponse:
    """Distinct values for the autocomplete of a filter (`kind`, `field`) or the value list of a
    column, limited to the devices (and, for endpoint / service values, the endpoints) the caller
    may read: the same restriction as the search itself, so nothing outside it can be discovered.

    `endpoint_read` must be given for endpoint and service values."""
    if kind == "field" and not field:
        raise APIError("field is required for 'field' values", 400)
    if needs_endpoint_permission([kind]) and endpoint_read is None:
        raise APIError("Endpoint permission is required to look up endpoint and service values", 403)

    allowed = await allowed_device_ids(device_repo, device_read, endpoint_read)
    # one more than asked for tells whether the list was cut short
    values = await repo.suggest_values(kind, field, query, limit + 1, allowed)
    return DeviceSearchValuesResponse(values=values[:limit], has_more=len(values) > limit)
