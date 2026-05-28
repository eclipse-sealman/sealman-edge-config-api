from typing import Any, Dict, List, Optional, cast

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.service import Service, ServiceType
from db.registry import register_repository
from db.repos.service import ServiceRepository
from db.merge import BlueprintResolver, patch_data, patch_fields
from exceptions import APIError


@register_repository(ServiceRepository)
class SqlAlchemyServiceRepository(BlueprintResolver, ServiceRepository):
    _ENTITY_ID_FIELD = "service_id"
    _PARENT_ID_FIELD = "endpoint_id"

    def __init__(self, session: AsyncSession):
        self._session = session

    async def _get_service_type_or_raise(self, type_id: str) -> ServiceType:
        result = await self._session.execute(
            select(ServiceType).where(ServiceType.type_id == type_id)
        )
        service_type = result.scalar_one_or_none()
        if service_type is None:
            raise APIError(f"ServiceType '{type_id}' not found", 404)
        return service_type

    async def _get_service(self, service_id: str) -> Optional[Service]:
        result = await self._session.execute(
            select(Service).where(Service.service_id == service_id)
        )
        return result.scalar_one_or_none()

    async def get_service_types(self) -> List[Dict[str, Any]]:
        result = await self._session.execute(select(ServiceType))
        return [self._serialize_type(st) for st in result.scalars().all()]

    async def get_service_type(self, type_id: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(ServiceType).where(ServiceType.type_id == type_id)
        )
        st = result.scalar_one_or_none()
        return self._serialize_type(st) if st else None

    async def create_service_type(
        self,
        type_id: str,
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        existing = await self._session.execute(
            select(ServiceType).where(ServiceType.type_id == type_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise APIError(f"ServiceType '{type_id}' already exists", 409)
        st = ServiceType(
            type_id=type_id, label=label, description=description, fields=fields or {}
        )
        self._session.add(st)
        await self._session.commit()
        await self._session.refresh(st)
        return self._serialize_type(st)

    async def update_service_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        st = await self._get_service_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None:
            values["fields"] = patch_fields(st.fields or {}, fields)
        if values:
            await self._session.execute(
                update(ServiceType)
                .where(ServiceType.type_id == type_id)
                .values(**values)
            )
            await self._session.commit()
            await self._session.refresh(st)
        return self._serialize_type(st)

    async def delete_service_type(self, type_id: str) -> None:
        await self._get_service_type_or_raise(type_id)
        await self._session.execute(
            delete(ServiceType).where(ServiceType.type_id == type_id)
        )
        await self._session.commit()

    async def get_services(
        self,
        endpoint_id: str,
        type_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(Service, ServiceType)
            .join(ServiceType, Service.type_id == ServiceType.type_id)
            .where(Service.endpoint_id == endpoint_id)
        )
        if type_id is not None:
            stmt = stmt.where(Service.type_id == type_id)
        result = await self._session.execute(stmt)
        return [
            self._serialize_resolved(service, service_type)
            for service, service_type in result.all()
        ]

    async def get_service(self, service_id: str) -> Optional[Dict[str, Any]]:
        result = await self._session.execute(
            select(Service, ServiceType)
            .join(ServiceType, Service.type_id == ServiceType.type_id)
            .where(Service.service_id == service_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        service, service_type = row
        return self._serialize_resolved(service, service_type)

    async def create_service(
        self,
        endpoint_id: str,
        type_id: str,
        service_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        service_type = await self._get_service_type_or_raise(type_id)
        service = Service(
            endpoint_id=endpoint_id, type_id=type_id, service_data=service_data or {}
        )
        self._session.add(service)
        await self._session.commit()
        await self._session.refresh(service)
        return self._serialize_resolved(service, service_type)

    async def update_service(
        self,
        service_id: str,
        service_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        service = await self._get_service(service_id)
        if service is None:
            return None
        await self._session.execute(
            update(Service)
            .where(Service.service_id == service_id)
            .values(service_data=patch_data(service.service_data or {}, service_data))
        )
        await self._session.commit()
        return await self.get_service(service_id)

    async def delete_service(self, service_id: str) -> None:
        await self._session.execute(
            delete(Service).where(Service.service_id == service_id)
        )
        await self._session.commit()
