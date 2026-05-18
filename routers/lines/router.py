from authorization import resource_types as Resource
from authorization.permission_check import PathParamPermissionCheck
from authorization.permission_types import Device
from fastapi import Depends
from routers.base_api_router import BaseAPIRouter

from routers.lines.routes.post_line_by_device_id import post_line as _post_line
from routers.lines.routes.get_line_by_device_id import get_line_by_device_id as _get_line_by_device_id
from routers.lines.routes.delete_line_by_device_id import delete_line_by_device_id as _delete_line_by_device_id
from routers.lines.routes.update_line_by_device_id import update_line_by_device_id as _update_line_by_id
from routers.lines.schemas import Line

lines = BaseAPIRouter()

@lines.get("/{device}/line/", response_model=Line, response_model_exclude_none=True, tags=["Lines"])
async def get_line_by_device_id(device: str, _ = Depends(PathParamPermissionCheck(Device.READ_LINE, Resource.DEVICE, "device"))):
    return await _get_line_by_device_id(device)

@lines.post("/{device}/line/", response_model=Line, response_model_exclude_none=True, tags=["Lines"])
async def post_line(device: str, line: Line, _ = Depends(PathParamPermissionCheck(Device.EDIT_LINE, Resource.DEVICE, "device"))):
    return await _post_line(device, line)

@lines.put("/{device}/line/", response_model=Line, response_model_exclude_none=True, tags=["Lines"])
async def update_line_by_id(device: str, line: Line, _ = Depends(PathParamPermissionCheck(Device.EDIT_LINE, Resource.DEVICE, "device"))):
    return await _update_line_by_id(device, line)

@lines.delete("/{device}/line/", response_model=None, status_code=204, tags=["Lines"])
async def delete_line_by_device_id(device: str, _ = Depends(PathParamPermissionCheck(Device.EDIT_LINE, Resource.DEVICE, "device"))):
    return await _delete_line_by_device_id(device)
