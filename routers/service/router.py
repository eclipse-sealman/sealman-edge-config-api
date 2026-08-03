from typing import List, Optional, Never
from fastapi import HTTPException, Depends, Query, APIRouter
from pydantic import ValidationError as PydanticValidationError
from exceptions import APIError
from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from db.repos.service import ServiceRepository
from db.session import get_repository
from db.merge import patch_fields, patch_data
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

# See routers/endpoint/router.py - same rationale: device/endpoint id arrives as a query
# param here, not a path param, so ABAC device-scope resolution is skipped explicitly.
_read = Depends(ABACPermissionCheck(Device.ENDPOINT_READ, device_path=None))
_write = Depends(ABACPermissionCheck(Device.ENDPOINT_WRITE, device_path=None))


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
    _auth=_read,
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
    _auth=_read,
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
    _auth=_write,
) -> ServiceTypeResponse:
    try:
        result = await repo.create_service_type(
            type_id=body.type_id,
            label=body.label,
            description=body.description,
            fields={k: v.model_dump(exclude_none=True) for k, v in body.fields.items()},
            mapping=body.mapping,
            browser_kind=body.browser_kind,
        )
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
    _auth=_write,
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
        if field_patch is not None or body.mapping is not None:
            current = await repo.get_service_type(type_id)
            if current is None:
                raise HTTPException(
                    status_code=404, detail=f"ServiceType '{type_id}' not found"
                )
            merged_fields = (
                patch_fields(current["fields"], field_patch)
                if field_patch is not None
                else current["fields"]
            )
            for field_key, field_dict in merged_fields.items():
                try:
                    FieldDefinition.model_validate(field_dict)
                except PydanticValidationError as exc:
                    raise HTTPException(
                        status_code=422, detail=f"Invalid field '{field_key}': {exc}"
                    )
            merged_mapping = patch_data(current["mapping"], body.mapping or {})
            unknown = set(merged_mapping) - set(merged_fields)
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"mapping references unknown field(s): {sorted(unknown)}",
                )
        result = await repo.update_service_type(
            type_id=type_id,
            label=body.label,
            description=body.description,
            fields=field_patch,
            mapping=body.mapping,
            browser_kind=body.browser_kind,
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
    _auth=_write,
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
    _auth=_read,
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
    _auth=_read,
) -> ServiceResponse:
    result = await repo.get_service(service_id=service_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Service '{service_id}' not found")
    return ServiceResponse.model_validate(result)


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
    _auth=_write,
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
    _auth=_write,
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
    _auth=_write,
) -> None:
    await repo.delete_service(service_id=service_id)
