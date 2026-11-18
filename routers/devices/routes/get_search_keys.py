from typing import Literal, Optional

from authorization.abac_permission_check import ABACDeviceListFilterResult
from db.repos.device import DeviceRepository
from db.repos.device_search import DeviceSearchRepository
from routers.devices.routes.search_devices import allowed_device_ids
from routers.devices.schemas import DeviceSearchKey, DeviceSearchKeysResponse

KEYS_LIMIT = 20


async def get_search_keys(
    repo: DeviceSearchRepository,
    device_repo: DeviceRepository,
    kind: Literal["endpoint", "service"],
    query: Optional[str],
    device_read: ABACDeviceListFilterResult,
    endpoint_read: ABACDeviceListFilterResult,
) -> DeviceSearchKeysResponse:
    """The fields endpoints / services have, to pick from when filtering on one field: the most
    used ones (matching `query`), capped at KEYS_LIMIT. Limited to what the caller may read, like
    the search itself."""
    allowed = await allowed_device_ids(device_repo, device_read, endpoint_read)
    # one more than asked for tells whether the list was cut short
    rows = await repo.list_field_keys(kind, query, KEYS_LIMIT + 1, allowed)
    keys = [DeviceSearchKey(key=row["key"], label=row["label"] or row["key"]) for row in rows[:KEYS_LIMIT]]
    return DeviceSearchKeysResponse(keys=keys, has_more=len(rows) > KEYS_LIMIT)
