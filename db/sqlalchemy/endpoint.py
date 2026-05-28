from typing import Any, Dict, List, Optional
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from db.models.endpoint import Endpoint, EndpointType
from db.registry import register_repository
from db.repos.endpoint import EndpointRepository
from db.merge import BlueprintResolver, patch_data, patch_fields
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
            values["fields"] = patch_fields(et.fields or {}, fields)
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
        self, device_id: str, type_id: str, endpoint_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        et = await self._get_endpoint_type_or_raise(type_id)
        endpoint = Endpoint(
            device_id=device_id, type_id=type_id, endpoint_data=endpoint_data or {}
        )
        self._session.add(endpoint)
        await self._session.commit()
        await self._session.refresh(endpoint)
        return self._serialize_resolved(endpoint, et)

    async def update_endpoint(
        self, endpoint_id: str, endpoint_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        endpoint = await self._get_endpoint(endpoint_id)
        if endpoint is None:
            return None
        await self._session.execute(
            update(Endpoint)
            .where(Endpoint.endpoint_id == endpoint_id)
            .values(
                endpoint_data=patch_data(endpoint.endpoint_data or {}, endpoint_data)
            )
        )
        await self._session.commit()
        return await self.get_endpoint(endpoint_id)

    async def delete_endpoint(self, endpoint_id: str) -> None:
        await self._session.execute(
            delete(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
        )
        await self._session.commit()
