from typing import List, Never
from fastapi import HTTPException, Depends, APIRouter
from pydantic import ValidationError as PydanticValidationError
from exceptions import APIError
from db.repos.device_type import DeviceTypeRepository
from db.session import get_repository
from db.merge import patch_fields
from routers.schemas import FieldDefinition
from routers.device_type.schemas import (
    DeviceTypeResponse,
    DeviceTypeCreate,
    DeviceTypeUpdate,
)

device_types = APIRouter()


def _handle_api_error(exc: APIError) -> Never:
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@device_types.get(
    "/device-types",
    response_model=List[DeviceTypeResponse],
    tags=["Device Types"],
    summary="List all device types",
)
async def list_device_types(
    repo: DeviceTypeRepository = Depends(get_repository(DeviceTypeRepository)),
):
    results = await repo.get_device_types()
    return [DeviceTypeResponse.model_validate(r) for r in results]


@device_types.get(
    "/device-types/{type_id}",
    response_model=DeviceTypeResponse,
    tags=["Device Types"],
    summary="Get a single device type",
)
async def get_device_type(
    type_id: str,
    repo: DeviceTypeRepository = Depends(get_repository(DeviceTypeRepository)),
) -> DeviceTypeResponse:
    result = await repo.get_device_type(type_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail=f"DeviceType '{type_id}' not found"
        )
    return DeviceTypeResponse.model_validate(result)


@device_types.post(
    "/device-types",
    response_model=DeviceTypeResponse,
    status_code=201,
    tags=["Device Types"],
    summary="Create a device type",
)
async def create_device_type(
    body: DeviceTypeCreate,
    repo: DeviceTypeRepository = Depends(get_repository(DeviceTypeRepository)),
) -> DeviceTypeResponse:
    try:
        result = await repo.create_device_type(
            type_id=body.type_id,
            label=body.label,
            description=body.description,
            fields={k: v.model_dump(exclude_none=True) for k, v in body.fields.items()},
        )
        return DeviceTypeResponse.model_validate(result)
    except APIError as exc:
        _handle_api_error(exc)


@device_types.patch(
    "/device-types/{type_id}",
    response_model=DeviceTypeResponse,
    tags=["Device Types"],
    summary="Update a device type",
)
async def update_device_type(
    type_id: str,
    body: DeviceTypeUpdate,
    repo: DeviceTypeRepository = Depends(get_repository(DeviceTypeRepository)),
) -> DeviceTypeResponse:
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
            current = await repo.get_device_type(type_id)
            if current is None:
                raise HTTPException(
                    status_code=404, detail=f"DeviceType '{type_id}' not found"
                )
            merged_fields = patch_fields(current["fields"], field_patch)
            for field_key, field_dict in merged_fields.items():
                try:
                    FieldDefinition.model_validate(field_dict)
                except PydanticValidationError as exc:
                    raise HTTPException(
                        status_code=422, detail=f"Invalid field '{field_key}': {exc}"
                    )
        result = await repo.update_device_type(
            type_id=type_id,
            label=body.label,
            description=body.description,
            fields=field_patch,
        )
    except APIError as exc:
        _handle_api_error(exc)

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"DeviceType '{type_id}' not found"
        )
    return DeviceTypeResponse.model_validate(result)


@device_types.delete(
    "/device-types/{type_id}",
    status_code=204,
    tags=["Device Types"],
    summary="Delete a device type",
)
async def delete_device_type(
    type_id: str,
    repo: DeviceTypeRepository = Depends(get_repository(DeviceTypeRepository)),
) -> None:
    try:
        await repo.delete_device_type(type_id)
    except APIError as exc:
        _handle_api_error(exc)
