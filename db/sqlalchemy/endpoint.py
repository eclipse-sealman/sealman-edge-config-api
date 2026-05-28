from typing import Any, Dict, List, Optional, cast

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.endpoint import Endpoint, EndpointType
from db.registry import register_repository
from db.repos.endpoint import EndpointRepository
from exceptions import APIError


@register_repository(EndpointRepository)
class SqlAlchemyEndpointRepository(EndpointRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _resolve(
        instance_data: Dict[str, Any],
        type_fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved: Dict[str, Any] = {}
        for field_key, field_def in type_fields.items():
            resolved[field_key] = {
                "value": instance_data.get(field_key),
                "field": field_def,
            }
        for data_key, data_value in instance_data.items():
            if data_key not in type_fields:
                resolved[data_key] = {"value": data_value, "field": None}
        return resolved

    async def _get_endpoint_type_or_raise(self, type_id: str) -> EndpointType:
        result = await self._session.execute(
            select(EndpointType).where(EndpointType.type_id == type_id)
        )
        endpoint_type = result.scalar_one_or_none()
        if endpoint_type is None:
            raise APIError(f"EndpointType '{type_id}' not found", 404)
        return endpoint_type

    async def _get_endpoint(self, endpoint_id: str) -> Optional[Endpoint]:
        result = await self._session.execute(
            select(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
        )
        return result.scalar_one_or_none()

    def _serialize_type(self, et: EndpointType) -> Dict[str, Any]:
        return {
            "type_id": et.type_id,
            "label": et.label,
            "description": et.description,
            "fields": et.fields or {},
            "created_at": et.created_at,
            "updated_at": et.updated_at,
        }

    def _serialize_resolved(
        self,
        endpoint: Endpoint,
        endpoint_type: EndpointType,
    ) -> Dict[str, Any]:
        return {
            "endpoint_id": endpoint.endpoint_id,
            "type_id": endpoint.type_id,
            "type_label": endpoint_type.label,
            "type_description": endpoint_type.description,
            "endpoint_data": self._resolve(
                cast(Dict[str, Any], endpoint.endpoint_data or {}),
                cast(Dict[str, Any], endpoint_type.fields or {}),
            ),
            "created_at": endpoint.created_at,
            "updated_at": endpoint.updated_at,
        }

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
        type_id: str,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        existing = await self._session.execute(
            select(EndpointType).where(EndpointType.type_id == type_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise APIError(f"EndpointType '{type_id}' already exists", 409)
        et = EndpointType(
            type_id=type_id, label=label, description=description, fields=fields or {}
        )
        self._session.add(et)
        await self._session.commit()
        await self._session.refresh(et)
        return self._serialize_type(et)

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
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None:
            current_fields = dict(et.fields or {})
            for field_key, field_patch in fields.items():
                if field_patch is None:
                    current_fields.pop(field_key, None)
                else:
                    existing = dict(current_fields.get(field_key, {}))
                    existing.update(
                        {k: v for k, v in field_patch.items() if v is not None}
                    )
                    current_fields[field_key] = existing
            values["fields"] = current_fields
        if values:
            await self._session.execute(
                update(EndpointType)
                .where(EndpointType.type_id == type_id)
                .values(**values)
            )
            await self._session.commit()
            await self._session.refresh(et)
        return self._serialize_type(et)

    async def delete_endpoint_type(self, type_id: str) -> None:
        await self._get_endpoint_type_or_raise(type_id)
        await self._session.execute(
            delete(EndpointType).where(EndpointType.type_id == type_id)
        )
        await self._session.commit()

    async def get_endpoints(
        self,
        device_id: str,
        type_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(Endpoint, EndpointType)
            .join(EndpointType, Endpoint.type_id == EndpointType.type_id)
            .where(Endpoint.device_id == device_id)
        )
        if type_id is not None:
            stmt = stmt.where(Endpoint.type_id == type_id)
        result = await self._session.execute(stmt)
        return [
            self._serialize_resolved(endpoint, endpoint_type)
            for endpoint, endpoint_type in result.all()
        ]

    async def get_endpoint(self, endpoint_id: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(Endpoint, EndpointType)
            .join(EndpointType, Endpoint.type_id == EndpointType.type_id)
            .where(Endpoint.endpoint_id == endpoint_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        endpoint, endpoint_type = row
        return self._serialize_resolved(endpoint, endpoint_type)

    async def create_endpoint(
        self,
        device_id: str,
        type_id: str,
        endpoint_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        endpoint_type = await self._get_endpoint_type_or_raise(type_id)
        endpoint = Endpoint(
            device_id=device_id, type_id=type_id, endpoint_data=endpoint_data or {}
        )
        self._session.add(endpoint)
        await self._session.commit()
        await self._session.refresh(endpoint)
        return self._serialize_resolved(endpoint, endpoint_type)

    async def update_endpoint(
        self,
        endpoint_id: str,
        endpoint_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        endpoint = await self._get_endpoint(endpoint_id)
        if endpoint is None:
            return None
        current_data = dict(cast(Dict[str, Any], endpoint.endpoint_data or {}))
        for key, value in endpoint_data.items():
            if value is None:
                current_data.pop(key, None)
            else:
                current_data[key] = value
        await self._session.execute(
            update(Endpoint)
            .where(Endpoint.endpoint_id == endpoint_id)
            .values(endpoint_data=current_data)
        )
        await self._session.commit()
        return await self.get_endpoint(endpoint_id)

    async def delete_endpoint(self, endpoint_id: str) -> None:
        await self._session.execute(
            delete(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
        )
        await self._session.commit()
