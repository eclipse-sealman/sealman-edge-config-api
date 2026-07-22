from fastapi import Depends, Request
from routers.base_api_router import BaseAPIRouter
from authorization.abac_permission_check import ABACDeviceListFilter, ABACDeviceListFilterResult
from authorization.permission_types import Device
from db.repos.device import DeviceRepository
from db.session import get_repository
from .routes.get_devices import get_devices, populate_cache_from_iot_hub_query
from .routes.create_device import create_device
from .routes.delete_device import delete_device
from .routes.get_device_meta_values import get_device_meta_values

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

@devices.put("/{device_id}")
async def create_device_route(
    device_id: str,
    request: Request,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    body = await request.json()
    return await create_device(device_id, body, repo)


@devices.delete("/{device_id}")
async def delete_device_route(
    device_id: str,
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    await delete_device(device_id, repo)
    return {"status": "deleted"}


@devices.get("/meta-values")
async def get_device_meta_values_route(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
):
    return await get_device_meta_values(repo)
