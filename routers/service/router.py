from typing import List, Optional, Never
from fastapi import HTTPException, Depends, Query, APIRouter
from pydantic import ValidationError as PydanticValidationError
from exceptions import APIError
from authorization.abac_permission_check import ABACPermissionCheck, ABACResolvedDeviceCheck
from authorization.permission_types import Device, Platform
from db.repos.service import UNSET, ServiceRepository
from db.repos.scan_ports import ScanPortsRepository
from db.session import get_repository
from db.merge import patch_fields
from routers.schemas import FieldDefinition
from routers.service.schemas import (
    ServiceTypeResponse,
    ServiceCreate,
    ServiceResponse,
    ServiceTypeCreate,
    ServiceTypeUpdate,
    ServiceUpdate,
)

services = APIRouter()

# Types are platform configuration: see routers/endpoint/router.py.
_type_read = Depends(ABACPermissionCheck(Device.ENDPOINT_READ, device_path=None))
_type_write = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None))

_endpoint_read = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_READ, "endpoint", "endpoint_id"))
_endpoint_write = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_WRITE, "endpoint", "endpoint_id"))
_service_read = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_READ, "service", "service_id"))
_service_write = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_WRITE, "service", "service_id"))


def _handle_api_error(exc: APIError) -> Never:
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@services.get(
    "/service-types",
    response_model=List[ServiceTypeResponse],
    tags=["Service Types"],
    summary="List all service types",
)
async def list_service_types(
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_type_read,
):
    results = await repo.get_service_types()
    return [ServiceTypeResponse.model_validate(r) for r in results]


@services.get(
    "/service-types/{type_id}",
    response_model=ServiceTypeResponse,
    tags=["Service Types"],
    summary="Get a single service type",
)
async def get_service_type(
    type_id: str,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_type_read,
) -> ServiceTypeResponse:
    result = await repo.get_service_type(type_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"ServiceType '{type_id}' not found"
        )
    return ServiceTypeResponse.model_validate(result)


@services.post(
    "/service-types",
    response_model=ServiceTypeResponse,
    status_code=201,
    tags=["Service Types"],
    summary="Create a service type",
)
async def create_service_type(
    body: ServiceTypeCreate,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    scan_ports_repo: ScanPortsRepository = Depends(get_repository(ScanPortsRepository)),
    _auth=_type_write,
) -> ServiceTypeResponse:
    try:
        result = await repo.create_service_type(
            label=body.label,
            description=body.description,
            fields={k: v.model_dump(exclude_none=True) for k, v in body.fields.items()},
            browser_kind=body.browser_kind,
            mapping=body.mapping,
        )
        # The default value of the type's `port`-role field (if any) is also always scanned on
        # every device at minimum - see get_network_scan_ports.py and default_scan_ports.py's
        # seed migration. The role decides, not the key name.
        port_key = next((key for key, role in body.mapping.items() if role == "port"), None)
        port_field = body.fields.get(port_key) if port_key is not None else None
        if port_field is not None and port_field.default is not None:
            try:
                await scan_ports_repo.add_default_port(int(port_field.default))
            except (TypeError, ValueError):
                pass
        return ServiceTypeResponse.model_validate(result)
    except APIError as exc:
        _handle_api_error(exc)


@services.patch(
    "/service-types/{type_id}",
    response_model=ServiceTypeResponse,
    tags=["Service Types"],
    summary="Update a service type",
)
async def update_service_type(
    type_id: str,
    body: ServiceTypeUpdate,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_type_write,
) -> ServiceTypeResponse:
    field_patch = (
        {
            k: v.model_dump(exclude_none=True) if v is not None else None
            for k, v in body.fields.items()
        }
        if body.fields is not None
        else None
    )
    try:
        if field_patch is not None:
            current = await repo.get_service_type(type_id)
            if current is None:
                raise HTTPException(
                    status_code=404, detail=f"ServiceType '{type_id}' not found"
                )
            merged_fields = patch_fields(current["fields"], field_patch)
            for field_key, field_dict in merged_fields.items():
                try:
                    FieldDefinition.model_validate(field_dict)
                except PydanticValidationError as exc:
                    raise HTTPException(
                        status_code=422, detail=f"Invalid field '{field_key}': {exc}"
                    )
        result = await repo.update_service_type(
            type_id=type_id,
            label=body.label,
            description=body.description,
            fields=field_patch,
            # Omitted keeps the stored value; an explicit null clears it.
            browser_kind=body.browser_kind if "browser_kind" in body.model_fields_set else UNSET,
            mapping=body.mapping,
        )
    except APIError as exc:
        _handle_api_error(exc)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"ServiceType '{type_id}' not found"
        )
    return ServiceTypeResponse.model_validate(result)


@services.delete(
    "/service-types/{type_id}",
    status_code=204,
    tags=["Service Types"],
    summary="Delete a service type",
)
async def delete_service_type(
    type_id: str,
    cascade: bool = False,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_type_write,
) -> None:
    try:
        await repo.delete_service_type(type_id, cascade=cascade)
    except APIError as exc:
        _handle_api_error(exc)


@services.get(
    "/services",
    response_model=List[ServiceResponse],
    tags=["Services"],
    summary="List all services for an endpoint",
)
async def list_services(
    endpoint_id: str = Query(description="Endpoint to list services for"),
    type_id: Optional[str] = Query(default=None, description="Filter by service type"),
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_endpoint_read,
) -> List[ServiceResponse]:
    results = await repo.get_services(endpoint_id=endpoint_id, type_id=type_id)
    return [ServiceResponse.model_validate(r) for r in results]


@services.get(
    "/services/{service_id}",
    response_model=ServiceResponse,
    tags=["Services"],
    summary="Get a single resolved service",
)
async def get_service(
    service_id: str,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_service_read,
) -> ServiceResponse:
    service = await repo.get_service(service_id=service_id)
    if service is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return ServiceResponse.model_validate(service)


@services.post(
    "/services",
    response_model=ServiceResponse,
    status_code=201,
    tags=["Services"],
    summary="Create a service for an endpoint",
)
async def create_service(
    body: ServiceCreate,
    endpoint_id: str = Query(description="Endpoint to create the service on"),
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_endpoint_write,
) -> ServiceResponse:
    try:
        result = await repo.create_service(
            endpoint_id=endpoint_id,
            type_id=body.type_id,
            service_data=body.service_data,
        )
        return ServiceResponse.model_validate(result)
    except APIError as exc:
        _handle_api_error(exc)


@services.patch(
    "/services/{service_id}",
    response_model=ServiceResponse,
    tags=["Services"],
    summary="Patch-update service data",
)
async def update_service(
    service_id: str,
    body: ServiceUpdate,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_service_write,
) -> ServiceResponse:
    try:
        result = await repo.update_service(
            service_id=service_id,
            service_data=body.service_data,
        )
    except (APIError, ValueError) as exc:
        if isinstance(exc, APIError):
            _handle_api_error(exc)
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return ServiceResponse.model_validate(result)


@services.delete(
    "/services/{service_id}",
    status_code=204,
    tags=["Services"],
    summary="Delete a service",
)
async def delete_service(
    service_id: str,
    repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    _auth=_service_write,
) -> None:
    await repo.delete_service(service_id=service_id)
