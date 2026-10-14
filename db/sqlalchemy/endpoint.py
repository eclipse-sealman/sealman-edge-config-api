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
from db.type_roles import ENDPOINT_ROLE_TYPES, drop_roles_of_missing_fields, validate_type_mapping
from exceptions import APIError


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
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self._raise_if_label_taken(label)
        # Admins define every field; nothing is added or forced here. Roles in `mapping`
        # ({field_key: role}) must point at fields of a fitting data type.
        type_fields = dict(fields or {})
        type_mapping = dict(mapping or {})
        validate_type_mapping(type_fields, type_mapping, ENDPOINT_ROLE_TYPES)
        et = EndpointType(
            label=label,
            description=description,
            fields=type_fields,
            mapping=type_mapping,
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
        mapping: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        et = await self._get_endpoint_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            await self._raise_if_label_taken(label, exclude_type_id=type_id)
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None or mapping is not None:
            merged_fields = (
                patch_fields(et.fields or {}, fields) if fields is not None else dict(et.fields or {})
            )
            # A sent mapping replaces the stored one as a whole. Otherwise the stored roles stay,
            # except those whose field this patch removed.
            new_mapping = (
                dict(mapping)
                if mapping is not None
                else drop_roles_of_missing_fields(et.mapping or {}, merged_fields)
            )
            validate_type_mapping(merged_fields, new_mapping, ENDPOINT_ROLE_TYPES)
            if fields is not None:
                values["fields"] = merged_fields
            values["mapping"] = new_mapping
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
            # Retyping keeps all existing data: the patch is merged onto the stored endpoint_data
            # (same rule as a normal update) and the result is validated against the new type.
            # Keys the new type defines become its field values; the rest stay visible as Custom
            # Fields (resolve_fields returns them with field=None). `changeable` isn't enforced
            # on retype, as before.
            new_et = await self._get_endpoint_type_or_raise(type_id)
            merged_data = patch_data(endpoint.endpoint_data or {}, endpoint_data or {})
            validate_instance_data(merged_data, new_et.fields or {})
            await self._session.execute(
                update(Endpoint)
                .where(Endpoint.endpoint_id == endpoint_id)
                .values(type_id=type_id, endpoint_data=merged_data)
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
