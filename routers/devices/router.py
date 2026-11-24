from typing import List, Literal, Optional
from fastapi import Depends, Query, Request
from auth import validate_jwt
from routers.base_api_router import BaseAPIRouter
from authorization.abac_permission_check import ABACDeviceListFilter, ABACDeviceListFilterResult
from authorization.permission_types import Device
from db.repos.device import DeviceRepository
from db.repos.device_search import DeviceSearchRepository
from db.repos.device_template import DeviceTemplateRepository
from db.repos.field_file import FieldFileRepository
from db.repos.user import UserRepository
from db.session import get_repository
from exceptions import APIError
from .routes.get_devices import get_devices, populate_cache_from_iot_hub_query
from .routes.get_devices_with_data import get_devices_with_data
from .routes.get_search_keys import get_search_keys
from .routes.get_search_values import COLUMN_VALUES_LIMIT, SUGGESTION_LIMIT, get_search_values
from .routes.search_devices import needs_endpoint_permission, search_devices
from .routes.create_device import create_device
from .routes.delete_device import delete_device
from .routes.get_device_meta_values import get_device_meta_values
from authorization.abac_permission_check import ABACPermissionCheck, ABACDeviceCreateCheck
from .routes.get_device import get_device
from routers.general.schemas import DeviceDetailResponse
from routers.devices.schemas import (
    MAX_VALUE_LENGTH,
    DeviceSearchKeysResponse,
    DeviceSearchRequest,
    DeviceSearchResponse,
    DeviceSearchValuesResponse,
    SearchFilterType,
)

devices = BaseAPIRouter(prefix="/devices", tags=["Devices"])

@devices.get("")
async def get_devices_route(
    request: Request,
    abac_context: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    return await get_devices(
        repo=repo,
        filter_device=abac_context["filter_device"],
        query_params=request.query_params,
    )

@devices.get("/with-data")
async def get_devices_with_data_route(
    request: Request,
    device_read: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    endpoint_read: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.ENDPOINT_READ)),
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    def visible(device_data: dict) -> bool:
        return device_read["filter_device"](device_data) and endpoint_read["filter_device"](device_data)

    return await get_devices_with_data(
        repo=repo,
        query_params=request.query_params,
        is_unrestricted=device_read["is_unrestricted"] and endpoint_read["is_unrestricted"],
        filter_device=visible,
    )

@devices.post("/search", response_model=DeviceSearchResponse)
async def search_devices_route(
    body: DeviceSearchRequest,
    auth_context: dict = Depends(validate_jwt),
    device_read: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    repo: DeviceSearchRepository = Depends(get_repository(DeviceSearchRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    # Endpoints and services are gated by their own permission, which is only required (403 when
    # no role grants it) if the caller actually searches them: a device.read-only user can still
    # search ids and metadata.
    endpoint_read = None
    if needs_endpoint_permission([f.type for f in body.filters]):
        endpoint_read = await ABACDeviceListFilter(Device.ENDPOINT_READ)(
            auth_context=auth_context, user_repo=user_repo
        )

    return await search_devices(
        repo=repo,
        device_repo=device_repo,
        request=body,
        device_read=device_read,
        endpoint_read=endpoint_read,
    )

@devices.get("/search/suggestions", response_model=DeviceSearchValuesResponse)
async def get_search_suggestions_route(
    type: SearchFilterType,
    q: str = Query(min_length=3, max_length=MAX_VALUE_LENGTH, description="At least 3 characters"),
    field: Optional[str] = Query(default=None, max_length=MAX_VALUE_LENGTH),
    auth_context: dict = Depends(validate_jwt),
    device_read: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    repo: DeviceSearchRepository = Depends(get_repository(DeviceSearchRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    query = q.strip()
    if len(query) < 3:
        raise APIError("Type at least 3 characters to get suggestions", 400)

    endpoint_read = None
    if needs_endpoint_permission([type]):
        endpoint_read = await ABACDeviceListFilter(Device.ENDPOINT_READ)(
            auth_context=auth_context, user_repo=user_repo
        )

    return await get_search_values(
        repo=repo,
        device_repo=device_repo,
        kind=type,
        field=field,
        query=query,
        limit=SUGGESTION_LIMIT,
        device_read=device_read,
        endpoint_read=endpoint_read,
    )

@devices.get("/search/keys", response_model=DeviceSearchKeysResponse)
async def get_search_keys_route(
    type: Literal["endpoint", "service"],
    q: Optional[str] = Query(default=None, max_length=MAX_VALUE_LENGTH),
    auth_context: dict = Depends(validate_jwt),
    device_read: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    user_repo: UserRepository = Depends(get_repository(UserRepository)),
    repo: DeviceSearchRepository = Depends(get_repository(DeviceSearchRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    endpoint_read = await ABACDeviceListFilter(Device.ENDPOINT_READ)(
        auth_context=auth_context, user_repo=user_repo
    )
    return await get_search_keys(
        repo=repo,
        device_repo=device_repo,
        kind=type,
        query=(q or "").strip() or None,
        device_read=device_read,
        endpoint_read=endpoint_read,
    )

@devices.get("/search/column-values", response_model=DeviceSearchValuesResponse)
async def get_column_values_route(
    field: str = Query(min_length=1, max_length=MAX_VALUE_LENGTH),
    q: Optional[str] = Query(default=None, max_length=MAX_VALUE_LENGTH),
    device_read: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    repo: DeviceSearchRepository = Depends(get_repository(DeviceSearchRepository)),
    device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    return await get_search_values(
        repo=repo,
        device_repo=device_repo,
        kind="field",
        field=field,
        query=(q or "").strip() or None,
        limit=COLUMN_VALUES_LIMIT,
        device_read=device_read,
        endpoint_read=None,
    )

@devices.put("/{device_id}")
async def create_device_route(
    device_id: str,
    request: Request,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    template_repo: DeviceTemplateRepository = Depends(get_repository(DeviceTemplateRepository)),
    _=Depends(ABACDeviceCreateCheck(Device.CREATE)),
):
    body = await request.json()
    return await create_device(device_id, body, repo, template_repo)


@devices.delete("/{device_id}")
async def delete_device_route(
    device_id: str,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    files: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _=Depends(ABACPermissionCheck(Device.DELETE, device_path="device_id")),
):
    await delete_device(device_id, repo)
    await files.delete_orphans()
    return {"status": "deleted"}


@devices.get("/meta-values")
async def get_device_meta_values_route(
    abac_context: ABACDeviceListFilterResult = Depends(ABACDeviceListFilter(Device.READ)),
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    return await get_device_meta_values(repo, abac_context["filter_device"])


@devices.get("/{device_id}", response_model=DeviceDetailResponse)
async def get_device_route(
    device_id: str,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    _=Depends(ABACPermissionCheck(Device.READ, device_path="device_id")),
):
    return await get_device(device_id, repo)
