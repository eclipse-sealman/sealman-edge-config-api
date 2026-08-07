from typing import Any, Dict, List, Optional
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.endpoint import Endpoint, EndpointType
from db.registry import register_repository
from db.repos.endpoint import EndpointRepository
from db.merge import (
    BlueprintResolver,
    patch_data,
    patch_fields,
    reject_non_changeable_updates,
    validate_instance_data,
)
from exceptions import APIError

# Every endpoint type automatically gets these fields - rather than letting an admin manually
# define/toggle them (see routers/endpoint/schemas.py's reserved-field checks, which reject any
# client-supplied field with a mismatched type at these keys):
# - "ip": required, non-changeable, mapped to the "ip" role for network discovery.
# - "name": required, but changeable (unlike "ip") so admins can rename an endpoint after it's
#   created - e.g. to tell apart two endpoints of the same type on one device. Defaults to
#   "Unnamed" so auto-created endpoints (see post_network_overview.py) always get a value, the
#   same way any other field's `default` is picked up by `_build_instance_data` there.
IP_FIELD_KEY = "ip"
NAME_FIELD_KEY = "name"
_IP_FIELD_DEFINITION: Dict[str, Any] = {
    "type": "string",
    "label": "IP Address",
    "required": True,
    "changeable": False,
    "ui": "input",
}
_NAME_FIELD_DEFINITION: Dict[str, Any] = {
    "type": "string",
    "label": "Name",
    "required": True,
    "changeable": True,
    "ui": "input",
    "default": "Unnamed",
}
_RESERVED_FIELD_DEFAULTS = {IP_FIELD_KEY: _IP_FIELD_DEFINITION, NAME_FIELD_KEY: _NAME_FIELD_DEFINITION}
_RESERVED_FIELD_OVERRIDES = {
    IP_FIELD_KEY: {"type": "string", "required": True, "changeable": False},
    NAME_FIELD_KEY: {"type": "string", "required": True, "changeable": True},
}


@register_repository(EndpointRepository)
class SqlAlchemyEndpointRepository(BlueprintResolver, EndpointRepository):
    _ENTITY_ID_FIELD = "endpoint_id"
    _PARENT_ID_FIELD = "device_id"

    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_endpoint_type_or_raise(self, type_id: str) -> EndpointType:
        result = await self._session.execute(
            select(EndpointType).where(EndpointType.type_id == type_id)
        )
        et = result.scalar_one_or_none()
        if et is None:
            raise APIError(f"EndpointType '{type_id}' not found", 404)
        return et

    async def _get_endpoint(self, endpoint_id: str) -> Optional[Endpoint]:
        result = await self._session.execute(
            select(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
        )
        return result.scalar_one_or_none()

    async def get_endpoint_types(self) -> List[Dict[str, Any]]:
        result = await self._session.execute(select(EndpointType))
        return [self._serialize_type(et) for et in result.scalars().all()]

    async def get_endpoint_type(self, type_id: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(EndpointType).where(EndpointType.type_id == type_id)
        )
        et = result.scalar_one_or_none()
        return self._serialize_type(et) if et else None

    async def create_endpoint_type(
        self,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        await self._raise_if_label_taken(label)
        # label/description/default/validation/ui may be admin-supplied (e.g. a default IP for
        # auto-discovery, see get_network_scan_range.py) - type/required/changeable are always
        # forced back to the fixed values regardless, so the client can't weaken them.
        all_fields = dict(fields or {})
        for key, base in _RESERVED_FIELD_DEFAULTS.items():
            all_fields[key] = {**base, **(fields.get(key) or {}), **_RESERVED_FIELD_OVERRIDES[key]}
        et = EndpointType(
            label=label,
            description=description,
            fields=all_fields,
            mapping={IP_FIELD_KEY: "ip"},
        )
        self._session.add(et)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise APIError(f"EndpointType label '{label}' already exists", 409)
        await self._session.refresh(et)
        return self._serialize_type(et)

    async def _raise_if_label_taken(
        self, label: str, exclude_type_id: Optional[str] = None
    ) -> None:
        stmt = select(EndpointType).where(EndpointType.label == label)
        if exclude_type_id is not None:
            stmt = stmt.where(EndpointType.type_id != exclude_type_id)
        existing = await self._session.execute(stmt)
        if existing.scalar_one_or_none() is not None:
            raise APIError(f"EndpointType label '{label}' already exists", 409)

    async def update_endpoint_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        et = await self._get_endpoint_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            await self._raise_if_label_taken(label, exclude_type_id=type_id)
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None:
            merged_fields = patch_fields(et.fields or {}, fields)
            # Restores a built-in field if a patch somehow removed it, and always re-forces its
            # structural properties - label/description/default/validation/ui stay whatever the
            # patch (or, failing that, the field's previous state) had.
            for key, base in _RESERVED_FIELD_DEFAULTS.items():
                current = merged_fields.get(key) or et.fields.get(key) or base
                merged_fields[key] = {**current, **_RESERVED_FIELD_OVERRIDES[key]}
            values["fields"] = merged_fields
        if values:
            try:
                await self._session.execute(
                    update(EndpointType)
                    .where(EndpointType.type_id == type_id)
                    .values(**values)
                )
                await self._session.commit()
            except IntegrityError:
                await self._session.rollback()
                raise APIError(f"EndpointType label '{label}' already exists", 409)
            await self._session.refresh(et)
        return self._serialize_type(et)

    async def delete_endpoint_type(self, type_id: str, cascade: bool = False) -> None:
        await self._get_endpoint_type_or_raise(type_id)
        if cascade:
            # Services cascade via their own FK to endpoints (ondelete=CASCADE).
            await self._session.execute(
                delete(Endpoint).where(Endpoint.type_id == type_id)
            )
        else:
            in_use = await self._session.execute(
                select(Endpoint.endpoint_id).where(Endpoint.type_id == type_id).limit(1)
            )
            if in_use.scalar_one_or_none() is not None:
                raise APIError(
                    f"EndpointType '{type_id}' is still in use by one or more endpoints", 409
                )
        await self._session.execute(
            delete(EndpointType).where(EndpointType.type_id == type_id)
        )
        await self._session.commit()

    async def get_endpoints(
        self, device_id: str, type_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(Endpoint, EndpointType)
            .join(EndpointType, Endpoint.type_id == EndpointType.type_id)
            .where(Endpoint.device_id == device_id)
        )
        if type_id is not None:
            stmt = stmt.where(Endpoint.type_id == type_id)
        result = await self._session.execute(stmt)
        return [self._serialize_resolved(e, et) for e, et in result.all()]

    async def get_endpoint(self, endpoint_id: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(Endpoint, EndpointType)
            .join(EndpointType, Endpoint.type_id == EndpointType.type_id)
            .where(Endpoint.endpoint_id == endpoint_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return self._serialize_resolved(*row)

    async def create_endpoint(
        self,
        device_id: str,
        type_id: str,
        endpoint_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        et = await self._get_endpoint_type_or_raise(type_id)
        validate_instance_data(endpoint_data or {}, et.fields or {})
        endpoint = Endpoint(
            device_id=device_id, type_id=type_id, endpoint_data=endpoint_data or {}
        )
        self._session.add(endpoint)
        await self._session.commit()
        await self._session.refresh(endpoint)
        return self._serialize_resolved(endpoint, et)

    async def update_endpoint(
        self,
        endpoint_id: str,
        endpoint_data: Optional[Dict[str, Any]] = None,
        type_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        endpoint = await self._get_endpoint(endpoint_id)
        if endpoint is None:
            return None

        if type_id is not None and type_id != endpoint.type_id:
            # Reassigning to a different type: the old endpoint_data was validated against a
            # different field schema, so it isn't merged forward - the caller supplies fresh
            # data for the new type (including a fresh "name"), validated the same way a
            # newly-created endpoint would be.
            new_et = await self._get_endpoint_type_or_raise(type_id)
            new_data = endpoint_data or {}
            validate_instance_data(new_data, new_et.fields or {})
            await self._session.execute(
                update(Endpoint)
                .where(Endpoint.endpoint_id == endpoint_id)
                .values(type_id=type_id, endpoint_data=new_data)
            )
        else:
            et = await self._get_endpoint_type_or_raise(endpoint.type_id)
            patch = endpoint_data or {}
            reject_non_changeable_updates(endpoint.endpoint_data or {}, patch, et.fields or {})
            merged_data = patch_data(endpoint.endpoint_data or {}, patch)
            validate_instance_data(merged_data, et.fields or {})
            await self._session.execute(
                update(Endpoint)
                .where(Endpoint.endpoint_id == endpoint_id)
                .values(endpoint_data=merged_data)
            )

        await self._session.commit()
        return await self.get_endpoint(endpoint_id)

    async def delete_endpoint(self, endpoint_id: str) -> None:
        await self._session.execute(
            delete(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
        )
        await self._session.commit()
