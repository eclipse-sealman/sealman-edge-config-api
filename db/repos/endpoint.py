from typing import List, Dict, Any, Optional, Tuple
from abc import ABC, abstractmethod


class EndpointRepository(ABC):
    @abstractmethod
    async def get_endpoint_types(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_endpoint_type(self, type_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_endpoint_type(
        self,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
        mapping: Optional[Dict[str, Any]] = None,
        managed_by: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_endpoint_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        mapping: Optional[Dict[str, Any]] = None,
        add_fields: Optional[Dict[str, Dict[str, Any]]] = None,
        add_managed_by: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """`add_fields` are inserted after the `fields` patch and `add_managed_by` is merged into
        the stored `managed_by`; a key of `add_fields` already in the stored fields raises
        APIError(422). Everything is written in one statement."""
        ...

    @abstractmethod
    async def apply_managed_fields(
        self,
        extension: str,
        type_ids: List[str],
        definitions: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, str]]]:
        """Copies each definition onto each type and marks it managed by `extension`.

        Raises APIError(404) for the first unknown type id before writing anything.
        Duplicate type ids are ignored. A key the type already has is skipped with
        reason "key_exists". Returns {"applied": [{type_id, key}], "skipped": [{type_id, key, reason}]}.
        """
        ...

    @abstractmethod
    async def release_managed_fields(self, extension: str, keys: Optional[List[str]] = None) -> None:
        """On every type, removes the `managed_by` entries owned by `extension`: all of them when
        `keys` is None, otherwise only those listed. The field definitions stay on the types."""
        ...

    @abstractmethod
    async def update_managed_field_definitions(
        self,
        extension: str,
        old_definitions: Dict[str, Dict[str, Any]],
        new_definitions: Dict[str, Dict[str, Any]],
    ) -> None:
        """On every type where a key of `new_definitions` is managed by `extension`, replaces the
        field's definition with the new one, keeping the admin's label, description and
        show_in_list where they differ from `old_definitions` (see `merge_changed_definition`).
        Never touches endpoint data."""
        ...

    @abstractmethod
    async def delete_endpoint_type(self, type_id: str, cascade: bool = False) -> None:
        """Deletes the type. If `cascade` is False and endpoints of this type still exist,
        raises APIError(409). If `cascade` is True, deletes those endpoints (and, via FK
        cascade, their services) first."""
        ...

    @abstractmethod
    async def get_endpoints(
        self,
        device_id: str,
        type_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_endpoints_by_types(
        self, device_id: str, type_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Raw rows `{endpoint_id, device_id, type_id, created_at, updated_at, endpoint_data}` of the
        device's endpoints whose type is in `type_ids`, with stored values only (no type defaults).
        Ordered by `created_at`, then `endpoint_id`. Empty `type_ids` gives []."""
        ...

    @abstractmethod
    async def page_devices_by_types(
        self, type_ids: List[str], non_empty_key: Optional[str], page: int, page_size: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Pages devices with at least one matching endpoint and returns `(rows, total_devices)`.

        An endpoint matches when its type is in `type_ids` and, if `non_empty_key` is set, its
        stored value for that key is neither missing, JSON `null` nor `""`. Devices are ordered
        by their earliest matching endpoint's `created_at` descending, then `device_id`.
        `rows` are the raw rows (as in `get_endpoints_by_types`) of the matching endpoints of
        the page's devices, ordered by device rank, then `created_at`, then `endpoint_id`.
        `total_devices` counts all devices with a matching endpoint. Empty `type_ids` gives ([], 0)."""
        ...

    @abstractmethod
    async def get_endpoint(self, endpoint_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def create_endpoint(
        self,
        device_id: str,
        type_id: str,
        endpoint_data: Dict[str, Any],
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_endpoint(
        self,
        endpoint_id: str,
        endpoint_data: Optional[Dict[str, Any]] = None,
        type_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def delete_endpoint(self, endpoint_id: str) -> None: ...
