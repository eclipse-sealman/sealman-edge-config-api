from fastapi import Depends, Request
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter
from authorization import resource_types as Resource
from authorization.permission_check import EntityLookup
from authorization.permission_types import Device
from db.repos.device import DeviceRepository
from routers.general.schemas import DeviceStatusWithConnectionList
from .routes.create_device import create_device as _create_device
from .routes.delete_device import delete_device as _delete_device
from .routes.get_devices import get_devices as _get_devices
from .routes.get_device_meta_values import get_device_meta_values as _get_device_meta_values

devices = BaseAPIRouter(prefix="/devices", tags=["Devices"])

@devices.get(
    "",
    response_model=DeviceStatusWithConnectionList,
    tags=["General"],
    openapi_extra={
        "parameters": [
            {
                "name": "meta",
                "in": "query",
                "required": False,
                "description": (
                    "Filter devices by metadata values. "
                    "Enter a JSON object where each key is a metadata field name. "
                    "Set a value to filter by exact match (e.g. {\"site\": \"Berlin\"}), "
                    "or set the value to an empty string to match any device where the key has a non-empty value (e.g. {\"site\": \"\"}). "
                    "Multiple keys are combined with AND."
                ),
                "style": "deepObject",
                "explode": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "string"
                    },
                    "example": {}
                },
            }
        ]
    },
)
async def get_devices(request: Request,
                      readable_devices = Depends(EntityLookup(Device.READ, Resource.DEVICE)),
                      repo: DeviceRepository = Depends(get_repository(DeviceRepository))):
    readable_device_map = {device: True for device in readable_devices}
    return await _get_devices(
        repo=repo,
        readable_device_map=readable_device_map,
        query_params=request.query_params,
    )

@devices.put("/{device_id}")
async def create_device(
    device_id: str,
    body: dict,
    repo=Depends(get_repository(DeviceRepository))
):
    return await _create_device(
        device_id=device_id,
        body=body,
        repo=repo
    )


@devices.delete("/{device_id}", status_code=204)
async def delete_device(
    device_id: str,
    repo=Depends(get_repository(DeviceRepository))
):
    return await _delete_device(
        device_id=device_id,
        repo=repo
    )


@devices.get("/metaValues")
async def get_device_meta_values(
    repo: DeviceRepository = Depends(get_repository(DeviceRepository))
):
    return await _get_device_meta_values(repo=repo)