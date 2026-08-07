from typing import Any, Dict, List, Optional, cast

from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.service import Service, ServiceType
from db.registry import register_repository
from db.repos.service import ServiceRepository
from db.merge import BlueprintResolver, patch_data, patch_fields, validate_instance_data
from exceptions import APIError

# Every service type automatically gets this field - required, non-changeable, mapped to the
# "port" role - rather than letting an admin manually pick/toggle which field plays that role
# (see routers/service/schemas.py's PORT_FIELD_KEY, which rejects any client-supplied field at
# this key).
PORT_FIELD_KEY = "port"
_PORT_FIELD_DEFINITION: Dict[str, Any] = {
    "type": "integer",
    "label": "Port",
    "required": True,
    "changeable": False,
    "ui": "number",
}


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

    def _serialize_type(self, entity_type: Any) -> Dict[str, Any]:
        result = super()._serialize_type(entity_type)
        result["browser_kind"] = entity_type.browser_kind
        return result

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
        label: str,
        description: Optional[str],
        fields: Dict[str, Any],
        browser_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._raise_if_label_taken(label)
        # label/description/default/validation/ui may be admin-supplied (e.g. a default port for
        # auto-discovery) - type/required/changeable are always forced back to the fixed values
        # regardless, so the client can't weaken them.
        port_field = {
            **_PORT_FIELD_DEFINITION,
            **(fields.get(PORT_FIELD_KEY) or {}),
            "type": "integer",
            "required": True,
            "changeable": False,
        }
        all_fields = {**(fields or {}), PORT_FIELD_KEY: port_field}
        st = ServiceType(
            label=label,
            description=description,
            fields=all_fields,
            mapping={PORT_FIELD_KEY: "port"},
            browser_kind=browser_kind,
        )
        self._session.add(st)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise APIError(f"ServiceType label '{label}' already exists", 409)
        await self._session.refresh(st)
        return self._serialize_type(st)

    async def _raise_if_label_taken(
        self, label: str, exclude_type_id: Optional[str] = None
    ) -> None:
        stmt = select(ServiceType).where(ServiceType.label == label)
        if exclude_type_id is not None:
            stmt = stmt.where(ServiceType.type_id != exclude_type_id)
        existing = await self._session.execute(stmt)
        if existing.scalar_one_or_none() is not None:
            raise APIError(f"ServiceType label '{label}' already exists", 409)

    async def update_service_type(
        self,
        type_id: str,
        label: Optional[str] = None,
        description: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        browser_kind: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        st = await self._get_service_type_or_raise(type_id)
        values: Dict[str, Any] = {}
        if label is not None:
            await self._raise_if_label_taken(label, exclude_type_id=type_id)
            values["label"] = label
        if description is not None:
            values["description"] = description
        if fields is not None:
            merged_fields = patch_fields(st.fields or {}, fields)
            # Restores the built-in field if a patch somehow removed it, and always re-forces
            # its structural properties - label/description/default/validation/ui stay whatever
            # the patch (or, failing that, the field's previous state) had.
            current_port = merged_fields.get(PORT_FIELD_KEY) or st.fields.get(PORT_FIELD_KEY) or _PORT_FIELD_DEFINITION
            merged_fields[PORT_FIELD_KEY] = {
                **current_port,
                "type": "integer",
                "required": True,
                "changeable": False,
            }
            values["fields"] = merged_fields
        # Always applied (even when None, to clear it back to "no browse action") - unlike the
        # fields above, there's no separate signal for "leave this unchanged" here.
        values["browser_kind"] = browser_kind
        if values:
            try:
                await self._session.execute(
                    update(ServiceType)
                    .where(ServiceType.type_id == type_id)
                    .values(**values)
                )
                await self._session.commit()
            except IntegrityError:
                await self._session.rollback()
                raise APIError(f"ServiceType label '{label}' already exists", 409)
            await self._session.refresh(st)
        return self._serialize_type(st)

    async def delete_service_type(self, type_id: str, cascade: bool = False) -> None:
        await self._get_service_type_or_raise(type_id)
        if cascade:
            await self._session.execute(
                delete(Service).where(Service.type_id == type_id)
            )
        else:
            in_use = await self._session.execute(
                select(Service.service_id).where(Service.type_id == type_id).limit(1)
            )
            if in_use.scalar_one_or_none() is not None:
                raise APIError(
                    f"ServiceType '{type_id}' is still in use by one or more services", 409
                )
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
        validate_instance_data(service_data or {}, service_type.fields or {})
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
        service_type = await self._get_service_type_or_raise(service.type_id)
        merged_data = patch_data(service.service_data or {}, service_data)
        validate_instance_data(merged_data, service_type.fields or {})
        await self._session.execute(
            update(Service)
            .where(Service.service_id == service_id)
            .values(service_data=merged_data)
        )
        await self._session.commit()
        return await self.get_service(service_id)

    async def delete_service(self, service_id: str) -> None:
        await self._session.execute(
            delete(Service).where(Service.service_id == service_id)
        )
        await self._session.commit()
