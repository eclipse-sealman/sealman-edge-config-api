from typing import List, Optional, Never
from fastapi import HTTPException, Depends, Query, APIRouter
from pydantic import ValidationError as PydanticValidationError
from exceptions import APIError
from authorization.abac_permission_check import ABACPermissionCheck
from authorization.device_access import DeviceAccess, device_access
from authorization.permission_types import Device, Platform
from db.repos.device import DeviceRepository
from db.repos.endpoint import EndpointRepository
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from db.merge import patch_fields
from extensions.endpoint_fields import resolve_extension_fields
from routers.schemas import FieldDefinition
from routers.endpoint.managed_field_lock import check_managed_field_patch
from routers.endpoint.schemas import (
    EndpointTypeResponse,
    EndpointCreate,
    EndpointResponse,
    EndpointTypeCreate,
    EndpointTypeUpdate,
    EndpointUpdate,
)

endpoints = APIRouter()

# Types are platform configuration: reading them stays at `device.endpoint.read` (not
# device-scoped, so the Settings page and every device view can load them), writing them needs
# `platform.config.write`.
_type_read = Depends(ABACPermissionCheck(Device.ENDPOINT_READ, device_path=None))
_type_write = Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None))

# Endpoint routes are checked against the endpoint's device. The device comes from the
# `device_id` query param or from the stored endpoint, so DeviceAccess.check() runs inside the
# handler (see authorization/device_access.py).
_read = Depends(device_access(Device.ENDPOINT_READ))
_write = Depends(device_access(Device.ENDPOINT_WRITE))


def _handle_api_error(exc: APIError) -> Never:
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@endpoints.get(
    "/endpoint-types",
    response_model=List[EndpointTypeResponse],
    tags=["Endpoint Types"],
    summary="List all endpoint types",
)
async def list_endpoint_types(
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    _auth=_type_read,
):
    results = await repo.get_endpoint_types()
    return [EndpointTypeResponse.model_validate(r) for r in results]


@endpoints.get(
    "/endpoint-types/{type_id}",
    response_model=EndpointTypeResponse,
    tags=["Endpoint Types"],
    summary="Get a single endpoint type",
)
async def get_endpoint_type(
    type_id: str,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    _auth=_type_read,
) -> EndpointTypeResponse:
    result = await repo.get_endpoint_type(type_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"EndpointType '{type_id}' not found"
        )
    return EndpointTypeResponse.model_validate(result)


@endpoints.post(
    "/endpoint-types",
    response_model=EndpointTypeResponse,
    status_code=201,
    tags=["Endpoint Types"],
    summary="Create an endpoint type",
)
async def create_endpoint_type(
    body: EndpointTypeCreate,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    _auth=_type_write,
) -> EndpointTypeResponse:
    own_fields = {k: v.model_dump(exclude_none=True) for k, v in body.fields.items()}
    try:
        added_fields, added_managed_by = await resolve_extension_fields(
            extension_repo, body.extension_fields, set(own_fields)
        )
        result = await repo.create_endpoint_type(
            label=body.label,
            description=body.description,
            fields={**own_fields, **added_fields},
            mapping=body.mapping,
            managed_by=added_managed_by,
        )
        return EndpointTypeResponse.model_validate(result)
    except APIError as exc:
        _handle_api_error(exc)


@endpoints.patch(
    "/endpoint-types/{type_id}",
    response_model=EndpointTypeResponse,
    tags=["Endpoint Types"],
    summary="Update an endpoint type",
)
async def update_endpoint_type(
    type_id: str,
    body: EndpointTypeUpdate,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    _auth=_type_write,
) -> EndpointTypeResponse:
    field_patch = (
        {
            k: v.model_dump(exclude_none=True) if v is not None else None
            for k, v in body.fields.items()
        }
        if body.fields is not None
        else None
    )
    try:
        # Checked first, so an unknown type gives its own 404 before any extension_fields error.
        current = await repo.get_endpoint_type(type_id)
        if current is None:
            raise HTTPException(
                status_code=404, detail=f"EndpointType '{type_id}' not found"
            )
        added_fields, added_managed_by = await resolve_extension_fields(
            extension_repo, body.extension_fields or {}, set(body.fields or {})
        )
        if field_patch is not None:
            merged_fields = patch_fields(current["fields"], field_patch)
            check_managed_field_patch(
                current["fields"], merged_fields, current.get("managed_by") or {}
            )
            for field_key, field_dict in merged_fields.items():
                try:
                    FieldDefinition.model_validate(field_dict)
                except PydanticValidationError as exc:
                    raise HTTPException(
                        status_code=422, detail=f"Invalid field '{field_key}': {exc}"
                    )
        result = await repo.update_endpoint_type(
            type_id=type_id,
            label=body.label,
            description=body.description,
            fields=field_patch,
            mapping=body.mapping,
            add_fields=added_fields,
            add_managed_by=added_managed_by,
        )
    except APIError as exc:
        _handle_api_error(exc)

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"EndpointType '{type_id}' not found"
        )
    return EndpointTypeResponse.model_validate(result)


@endpoints.delete(
    "/endpoint-types/{type_id}",
    status_code=204,
    tags=["Endpoint Types"],
    summary="Delete an endpoint type",
)
async def delete_endpoint_type(
    type_id: str,
    cascade: bool = False,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    _auth=_type_write,
) -> None:
    try:
        await repo.delete_endpoint_type(type_id, cascade=cascade)
    except APIError as exc:
        _handle_api_error(exc)


@endpoints.get(
    "/endpoints",
    response_model=List[EndpointResponse],
    tags=["Endpoints"],
    summary="List all endpoints for a device",
)
async def list_endpoints(
    device_id: str = Query(description="Device to list endpoints for"),
    type_id: Optional[str] = Query(default=None, description="Filter by endpoint type"),
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    access: DeviceAccess = _read,
) -> List[EndpointResponse]:
    await access.check(device_id)
    results = await repo.get_endpoints(device_id=device_id, type_id=type_id)
    return [EndpointResponse.model_validate(r) for r in results]


async def _get_endpoint_checked(
    endpoint_id: str, repo: EndpointRepository, access: DeviceAccess
) -> dict:
    endpoint = await repo.get_endpoint(endpoint_id=endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint_id}' not found")
    await access.check(endpoint["device_id"])
    return endpoint


@endpoints.get(
    "/endpoints/{endpoint_id}",
    response_model=EndpointResponse,
    tags=["Endpoints"],
    summary="Get a single resolved endpoint",
)
async def get_endpoint(
    endpoint_id: str,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    access: DeviceAccess = _read,
) -> EndpointResponse:
    return EndpointResponse.model_validate(await _get_endpoint_checked(endpoint_id, repo, access))


@endpoints.post(
    "/endpoints",
    response_model=EndpointResponse,
    status_code=201,
    tags=["Endpoints"],
    summary="Create an endpoint for a device",
)
async def create_endpoint(
    body: EndpointCreate,
    device_id: str = Query(description="Device to create the endpoint on"),
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    access: DeviceAccess = _write,
) -> EndpointResponse:
    await access.check(device_id)
    # Admins and unscoped users pass the check for any device id, so a missing device must be
    # caught here; otherwise the insert fails on the foreign key and returns 500.
    if await device_repo.get_device_meta_raw(device_id) is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    try:
        result = await repo.create_endpoint(
            device_id=device_id,
            type_id=body.type_id,
            endpoint_data=body.endpoint_data,
        )
        return EndpointResponse.model_validate(result)
    except APIError as exc:
        _handle_api_error(exc)


@endpoints.patch(
    "/endpoints/{endpoint_id}",
    response_model=EndpointResponse,
    tags=["Endpoints"],
    summary="Patch-update endpoint data",
)
async def update_endpoint(
    endpoint_id: str,
    body: EndpointUpdate,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    access: DeviceAccess = _write,
) -> EndpointResponse:
    await _get_endpoint_checked(endpoint_id, repo, access)
    try:
        result = await repo.update_endpoint(
            endpoint_id=endpoint_id,
            endpoint_data=body.endpoint_data,
            type_id=body.type_id,
        )
    except (APIError, ValueError) as exc:
        if isinstance(exc, APIError):
            _handle_api_error(exc)
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Endpoint '{endpoint_id}' not found"
        )
    return EndpointResponse.model_validate(result)


@endpoints.delete(
    "/endpoints/{endpoint_id}",
    status_code=204,
    tags=["Endpoints"],
    summary="Delete an endpoint",
)
async def delete_endpoint(
    endpoint_id: str,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
    access: DeviceAccess = _write,
) -> None:
    await _get_endpoint_checked(endpoint_id, repo, access)
    await repo.delete_endpoint(endpoint_id=endpoint_id)
