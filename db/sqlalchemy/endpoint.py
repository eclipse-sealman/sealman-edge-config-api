import copy
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import delete, distinct, func, select, update
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
from db.managed_fields import merge_changed_definition, reject_roles_on_managed_fields
from db.transaction import commit_session
from db.type_roles import ENDPOINT_ROLE_TYPES, drop_roles_of_missing_fields, validate_type_mapping
from exceptions import APIError, ValidationError


@register_repository(EndpointRepository)
class SqlAlchemyEndpointRepository(BlueprintResolver, EndpointRepository):
    _ENTITY_ID_FIELD = "endpoint_id"
    _PARENT_ID_FIELD = "device_id"

    def __init__(self, session: AsyncSession):
        self._session = session

    def _serialize_type(self, entity_type: Any) -> Dict[str, Any]:
        result = super()._serialize_type(entity_type)
        result["managed_by"] = entity_type.managed_by or {}
        return result

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
        managed_by: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        await self._raise_if_label_taken(label)
        # Admins define every field; nothing is added or forced here. Roles in `mapping`
        # ({field_key: role}) must point at fields of a fitting data type.
        type_fields = dict(fields or {})
        type_mapping = dict(mapping or {})
        type_managed_by = dict(managed_by or {})
        validate_type_mapping(type_fields, type_mapping, ENDPOINT_ROLE_TYPES)
        reject_roles_on_managed_fields(type_mapping, type_managed_by)
        et = EndpointType(
            label=label,
            description=description,
            fields=type_fields,
            mapping=type_mapping,
            managed_by=type_managed_by,
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
        add_fields: Optional[Dict[str, Dict[str, Any]]] = None,
        add_managed_by: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        et = await self._get_endpoint_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            await self._raise_if_label_taken(label, exclude_type_id=type_id)
            values["label"] = label
        if description is not None:
            values["description"] = description
        for key in add_fields or {}:
            if key in (et.fields or {}):
                raise ValidationError(f"'{key}' already exists on this type", 422)
        if fields is not None or mapping is not None or add_fields or add_managed_by:
            # New dicts: the stored JSONB values are never mutated in place.
            merged_fields = (
                patch_fields(et.fields or {}, fields) if fields is not None else dict(et.fields or {})
            )
            for key, definition in (add_fields or {}).items():
                merged_fields[key] = copy.deepcopy(definition)
            # A removed field takes its marker with it: it is no longer managed (D13).
            new_managed_by = {
                **drop_roles_of_missing_fields(et.managed_by or {}, merged_fields),
                **(add_managed_by or {}),
            }
            # A sent mapping replaces the stored one as a whole. Otherwise the stored roles stay,
            # except those whose field this patch removed.
            new_mapping = (
                dict(mapping)
                if mapping is not None
                else drop_roles_of_missing_fields(et.mapping or {}, merged_fields)
            )
            validate_type_mapping(merged_fields, new_mapping, ENDPOINT_ROLE_TYPES)
            reject_roles_on_managed_fields(new_mapping, new_managed_by)
            if fields is not None or add_fields:
                values["fields"] = merged_fields
            if new_managed_by != (et.managed_by or {}):
                values["managed_by"] = new_managed_by
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

    async def apply_managed_fields(
        self,
        extension: str,
        type_ids: List[str],
        definitions: Dict[str, Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, str]]]:
        unique_ids = list(dict.fromkeys(type_ids))
        # Check every id before writing, so an unknown one leaves all types unchanged.
        types = [await self._get_endpoint_type_or_raise(type_id) for type_id in unique_ids]

        applied: List[Dict[str, str]] = []
        skipped: List[Dict[str, str]] = []
        for et in types:
            # New dicts: the stored JSONB values are never mutated in place.
            new_fields = dict(et.fields or {})
            new_managed_by = dict(et.managed_by or {})
            for key, definition in definitions.items():
                if key in new_fields:
                    skipped.append({"type_id": et.type_id, "key": key, "reason": "key_exists"})
                    continue
                new_fields[key] = copy.deepcopy(definition)
                new_managed_by[key] = extension
                applied.append({"type_id": et.type_id, "key": key})
            if new_managed_by != (et.managed_by or {}):
                await self._session.execute(
                    update(EndpointType)
                    .where(EndpointType.type_id == et.type_id)
                    .values(fields=new_fields, managed_by=new_managed_by)
                )
        await commit_session(self._session)
        return {"applied": applied, "skipped": skipped}

    async def release_managed_fields(self, extension: str, keys: Optional[List[str]] = None) -> None:
        released = None if keys is None else set(keys)
        result = await self._session.execute(select(EndpointType))
        for et in result.scalars().all():
            current = et.managed_by or {}
            # A new dict: the stored JSONB value is never mutated in place.
            new_managed_by = {
                key: owner
                for key, owner in current.items()
                if owner != extension or (released is not None and key not in released)
            }
            if new_managed_by != current:
                await self._session.execute(
                    update(EndpointType)
                    .where(EndpointType.type_id == et.type_id)
                    .values(managed_by=new_managed_by)
                )
        await commit_session(self._session)

    async def update_managed_field_definitions(
        self,
        extension: str,
        old_definitions: Dict[str, Dict[str, Any]],
        new_definitions: Dict[str, Dict[str, Any]],
    ) -> None:
        result = await self._session.execute(select(EndpointType))
        for et in result.scalars().all():
            managed_by = et.managed_by or {}
            current_fields = et.fields or {}
            # A new dict: the stored JSONB value is never mutated in place.
            new_fields = dict(current_fields)
            for key, new_def in new_definitions.items():
                if managed_by.get(key) != extension or key not in current_fields:
                    continue
                new_fields[key] = merge_changed_definition(
                    current_fields[key], old_definitions.get(key) or {}, new_def
                )
            if new_fields != current_fields:
                await self._session.execute(
                    update(EndpointType)
                    .where(EndpointType.type_id == et.type_id)
                    .values(fields=new_fields)
                )
        await commit_session(self._session)

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

    async def get_endpoints_by_types(
        self, device_id: str, type_ids: List[str]
    ) -> List[Dict[str, Any]]:
        if not type_ids:
            return []
        result = await self._session.execute(
            select(Endpoint)
            .where(Endpoint.device_id == device_id, Endpoint.type_id.in_(type_ids))
            .order_by(Endpoint.created_at, Endpoint.endpoint_id)
        )
        return [self._serialize_raw(e) for e in result.scalars().all()]

    async def page_devices_by_types(
        self, type_ids: List[str], non_empty_key: Optional[str], page: int, page_size: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        if not type_ids:
            return [], 0
        matching = [Endpoint.type_id.in_(type_ids)]
        if non_empty_key is not None:
            # `->>` gives SQL NULL for a missing key and for JSON null, and '' for "".
            # `false` and `0` read as 'false' and '0', so they match.
            value = Endpoint.endpoint_data[non_empty_key].astext
            matching += [value.isnot(None), value != ""]

        total = await self._session.scalar(
            select(func.count(distinct(Endpoint.device_id))).where(*matching)
        )
        rank = func.min(Endpoint.created_at).label("rank")
        ranked = (
            select(Endpoint.device_id, rank)
            .where(*matching)
            .group_by(Endpoint.device_id)
            .order_by(rank.desc(), Endpoint.device_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .subquery()
        )
        result = await self._session.execute(
            select(Endpoint)
            .join(ranked, Endpoint.device_id == ranked.c.device_id)
            .where(*matching)
            .order_by(
                ranked.c.rank.desc(),
                ranked.c.device_id,
                Endpoint.created_at,
                Endpoint.endpoint_id,
            )
        )
        return [self._serialize_raw(e) for e in result.scalars().all()], total or 0

    @staticmethod
    def _serialize_raw(endpoint: Endpoint) -> Dict[str, Any]:
        return {
            "endpoint_id": endpoint.endpoint_id,
            "device_id": endpoint.device_id,
            "type_id": endpoint.type_id,
            "created_at": endpoint.created_at,
            "updated_at": endpoint.updated_at,
            "endpoint_data": endpoint.endpoint_data or {},
        }

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
