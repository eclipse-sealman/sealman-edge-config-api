from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

SearchKind = Literal["device_id", "field", "metadata", "endpoint", "service"]


@dataclass(frozen=True)
class SearchFilter:
    """One search condition on a device.

    - device_id: the device id equals `value` (case-insensitive)
    - field:     the metadata field `field` equals `value` (case-insensitive)
    - metadata:  any metadata value contains `value` (case-insensitive)
    - endpoint:  any value of one of the device's endpoints, or an endpoint type label, contains `value`
    - service:   any value of one of the device's services, or a service type label, contains `value`
    """

    kind: SearchKind
    value: str
    field: Optional[str] = None


@dataclass(frozen=True)
class ColumnFilter:
    """The metadata field `field` equals ANY of `values` (case-sensitive: the values come from
    `suggest_values`)."""

    field: str
    values: List[str]


class DeviceSearchRepository(ABC):
    """Search over devices, their metadata, endpoints and services.

    Metadata, endpoint and service values are matched as the API resolves them, i.e. the value
    stored on the instance, or the type's default when none is stored.

    Every method takes an optional `allowed_device_ids` that restricts it to those devices
    (None = no restriction; an empty list matches nothing). Authorization is the caller's job:
    it computes the allowed devices with the existing ABAC filters and passes them in, so
    nothing outside them is ever matched, listed or suggested.
    """

    @abstractmethod
    async def find_device_ids(
        self,
        filters: List[SearchFilter],
        column_filters: List[ColumnFilter],
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[str]:
        """Ids of the devices satisfying ALL `filters` AND ALL `column_filters`, ordered by id.
        Built as one query; each device appears once."""
        ...

    @abstractmethod
    async def suggest_values(
        self,
        kind: SearchKind,
        field: Optional[str],
        query: Optional[str],
        limit: int,
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[str]:
        """Distinct values a filter of `kind` could use (device ids, the values of the metadata
        field `field`, any metadata / endpoint / service value), containing `query` when given,
        alphabetical, at most `limit`."""
        ...

    @abstractmethod
    async def get_device_fields(
        self,
        field_keys: List[str],
        device_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """The values of metadata fields `field_keys` on `device_ids`, one row each:
        device_id, field_key, field_label, value. Fields without a value are left out."""
        ...

    @abstractmethod
    async def search_device_metadata(
        self,
        terms: List[str],
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Metadata fields containing any of `terms`, one row each: device_id, field_key,
        field_label, value."""
        ...

    @abstractmethod
    async def search_endpoints(
        self,
        terms: List[str],
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Endpoint fields (or the endpoint type's label, with field_key None) containing any of
        `terms`, one row each: device_id, endpoint_id, type_label, endpoint_name, field_key,
        field_label, value."""
        ...

    @abstractmethod
    async def search_services(
        self,
        terms: List[str],
        allowed_device_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Service fields (or the service type's label, with field_key None) containing any of
        `terms`, one row each: device_id, endpoint_id, endpoint_name, service_id, type_label,
        field_key, field_label, value."""
        ...
