from typing import Dict, List, Optional

from authorization.abac_permission_check import ABACDeviceListFilterResult
from db.repos.device import DeviceRepository
from db.repos.device_search import ColumnFilter, DeviceSearchRepository, SearchFilter
from exceptions import APIError
from routers.devices.schemas import (
    DeviceSearchMatch,
    DeviceSearchMatchField,
    DeviceSearchRequest,
    DeviceSearchResponse,
    DeviceSearchResult,
)

ENDPOINT_KINDS = ("endpoint", "service")

_TYPE_LABEL = "Type"


def needs_endpoint_permission(kinds: List[str]) -> bool:
    return any(kind in ENDPOINT_KINDS for kind in kinds)


async def allowed_device_ids(
    device_repo: DeviceRepository,
    device_read: ABACDeviceListFilterResult,
    endpoint_read: Optional[ABACDeviceListFilterResult],
) -> Optional[List[str]]:
    """The devices a search / suggestion may look at; None = unrestricted.

    Those the caller may read and, when `endpoint_read` is given (the search touches endpoints or
    services), may read the endpoints of: the same rule as GET /devices/with-data. Scope filtering
    happens in Python on the devices' raw metadata, as everywhere else, so scoped callers load the
    device list once. The result is handed to the SQL query, which never looks beyond it."""
    if device_read["is_unrestricted"] and (endpoint_read is None or endpoint_read["is_unrestricted"]):
        return None

    devices = await device_repo.get_all_devices_raw()
    allowed = []
    for device in devices:
        if not device_read["filter_device"](device["device_data"]):
            continue
        if endpoint_read is not None and not endpoint_read["filter_device"](device["device_data"]):
            continue
        allowed.append(device["device_id"])
    return allowed


def _label(row: Dict[str, Optional[str]]) -> str:
    # Fields stored on an instance but not declared by its type have no label: show their key.
    return row["field_label"] or row["field_key"] or _TYPE_LABEL


def _field(row: Dict[str, Optional[str]]) -> DeviceSearchMatchField:
    return DeviceSearchMatchField(key=row["field_key"], label=_label(row), value=row["value"] or "")


async def search_devices(
    repo: DeviceSearchRepository,
    device_repo: DeviceRepository,
    request: DeviceSearchRequest,
    device_read: ABACDeviceListFilterResult,
    endpoint_read: Optional[ABACDeviceListFilterResult],
) -> DeviceSearchResponse:
    """Devices satisfying ALL the request's filters AND column filters, each with what matched.

    `endpoint_read` must be given whenever there is an endpoint or service filter."""
    if not request.filters and not request.column_filters:
        raise APIError("At least one filter is required", 400)
    if needs_endpoint_permission([f.type for f in request.filters]) and endpoint_read is None:
        raise APIError("Endpoint permission is required to search endpoints and services", 403)

    allowed = await allowed_device_ids(device_repo, device_read, endpoint_read)
    device_ids = await repo.find_device_ids(
        [SearchFilter(kind=f.type, value=f.value, field=f.field) for f in request.filters],
        [ColumnFilter(field=c.field, values=c.values) for c in request.column_filters],
        allowed,
    )
    if not device_ids:
        return DeviceSearchResponse(total=0, items=[])

    # What matched, for the devices found: one query per kind of filter in use, never per device.
    # A kind with several filters lists what matched any of them (the AND is across devices).
    values_of = lambda kind: [f.value for f in request.filters if f.type == kind and f.field is None]  # noqa: E731
    keyed_of = lambda kind: [  # noqa: E731
        (f.field, f.value) for f in request.filters if f.type == kind and f.field is not None
    ]
    matches: Dict[str, List[DeviceSearchMatch]] = {device_id: [] for device_id in device_ids}

    if values_of("device_id"):
        for device_id in device_ids:
            matches[device_id].append(
                DeviceSearchMatch(
                    kind="device_id",
                    fields=[DeviceSearchMatchField(key="device_id", label="Device ID", value=device_id)],
                )
            )

    metadata_by_device: Dict[str, Dict[str, DeviceSearchMatchField]] = {}
    field_keys = sorted({f.field for f in request.filters if f.type == "field" and f.field})
    rows = await repo.get_device_fields(field_keys, device_ids)
    if values_of("metadata"):
        rows += await repo.search_device_metadata(values_of("metadata"), device_ids)
    for row in rows:
        metadata_by_device.setdefault(row["device_id"], {}).setdefault(row["field_key"], _field(row))
    for device_id, fields in metadata_by_device.items():
        matches[device_id].append(DeviceSearchMatch(kind="metadata", fields=list(fields.values())))

    endpoint_matches: Dict[str, DeviceSearchMatch] = {}
    for row in await repo.search_endpoints(values_of("endpoint"), device_ids, keyed_of("endpoint")):
        match = endpoint_matches.get(row["endpoint_id"])
        if match is None:
            match = DeviceSearchMatch(
                kind="endpoint",
                fields=[],
                endpoint_id=row["endpoint_id"],
                endpoint_name=row["endpoint_name"],
                type_label=row["type_label"],
            )
            endpoint_matches[row["endpoint_id"]] = match
            matches[row["device_id"]].append(match)
        match.fields.append(_field(row))

    service_matches: Dict[str, DeviceSearchMatch] = {}
    for row in await repo.search_services(values_of("service"), device_ids, keyed_of("service")):
        match = service_matches.get(row["service_id"])
        if match is None:
            match = DeviceSearchMatch(
                kind="service",
                fields=[],
                endpoint_id=row["endpoint_id"],
                endpoint_name=row["endpoint_name"],
                type_label=row["type_label"],
                service_id=row["service_id"],
            )
            service_matches[row["service_id"]] = match
            matches[row["device_id"]].append(match)
        match.fields.append(_field(row))

    items = [DeviceSearchResult(device_id=device_id, matches=matches[device_id]) for device_id in device_ids]
    return DeviceSearchResponse(total=len(items), items=items)
