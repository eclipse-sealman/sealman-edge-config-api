from typing import List, Optional, Never
from fastapi import HTTPException, Depends, Query, APIRouter
from exceptions import APIError
from db.repos.endpoint import EndpointRepository
from db.session import get_repository
from routers.endpoint.schemas import (
    EndpointTypeResponse,
    EndpointCreate,
    EndpointResponse,
    EndpointTypeCreate,
    EndpointTypeUpdate,
    EndpointUpdate,
)

endpoints = APIRouter()


def _handle_api_error(exc: APIError) -> Never:
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@endpoints.get(
    "/endpoint-types",
    response_model=List[EndpointTypeResponse],
    tags=["Endpoints"],
    summary="List all endpoint types",
)
async def list_endpoint_types(
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
):
    results = await repo.get_endpoint_types()
    return [EndpointTypeResponse.model_validate(r) for r in results]


@endpoints.get(
    "/endpoint-types/{type_id}",
    response_model=EndpointTypeResponse,
    tags=["Endpoints"],
    summary="Get a single endpoint type",
)
async def get_endpoint_type(
    type_id: str,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
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
    tags=["Endpoints"],
    summary="Create an endpoint type",
)
async def create_endpoint_type(
    body: EndpointTypeCreate,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
) -> EndpointTypeResponse:
    try:
        result = await repo.create_endpoint_type(
            type_id=body.type_id,
            label=body.label,
            description=body.description,
            fields={k: v.model_dump(exclude_none=True) for k, v in body.fields.items()},
        )
        return EndpointTypeResponse.model_validate(result)
    except APIError as exc:
        _handle_api_error(exc)


@endpoints.patch(
    "/endpoint-types/{type_id}",
    response_model=EndpointTypeResponse,
    tags=["Endpoints"],
    summary="Update an endpoint type",
)
async def update_endpoint_type(
    type_id: str,
    body: EndpointTypeUpdate,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
) -> EndpointTypeResponse:
    try:
        result = await repo.update_endpoint_type(
            type_id=type_id,
            label=body.label,
            description=body.description,
            fields={
                k: v.model_dump(exclude_none=True) if v is not None else None
                for k, v in body.fields.items()
            }
            if body.fields is not None
            else None,
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
    tags=["Endpoints"],
    summary="Delete an endpoint type",
)
async def delete_endpoint_type(
    type_id: str,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
) -> None:
    try:
        await repo.delete_endpoint_type(type_id)
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
) -> List[EndpointResponse]:
    results = await repo.get_endpoints(device_id=device_id, type_id=type_id)
    return [EndpointResponse.model_validate(r) for r in results]


@endpoints.get(
    "/endpoints/{endpoint_id}",
    response_model=EndpointResponse,
    tags=["Endpoints"],
    summary="Get a single resolved endpoint",
)
async def get_endpoint(
    endpoint_id: str,
    repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
) -> EndpointResponse:
    result = await repo.get_endpoint(endpoint_id=endpoint_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"Endpoint '{endpoint_id}' not found"
        )
    return EndpointResponse.model_validate(result)


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
) -> EndpointResponse:
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
) -> EndpointResponse:
    try:
        result = await repo.update_endpoint(
            endpoint_id=endpoint_id,
            endpoint_data=body.endpoint_data,
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
) -> None:
    await repo.delete_endpoint(endpoint_id=endpoint_id)
