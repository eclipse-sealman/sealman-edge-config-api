from fastapi import Depends, Response

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from db.repos.password_renewal_task import PasswordRenewalTaskRepository
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter
from routers.password.schemas import (
    DeviceSecretInformation,
    DeviceSecretValue,
    PasswordRenewalAccepted,
)
from routers.password.service import get_password_info, request_password, renew_password


def prevent_password_caching(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def is_password_path(path: str) -> bool:
    parts = path.rstrip("/").split("/")
    return (
        len(parts) >= 3
        and parts[-1] in {"info", "request", "renew"}
        and (
            parts[-2] == "password"
            or (len(parts) >= 4 and parts[-3:-1] == ["smartems", "secret"])
        )
    )


password = BaseAPIRouter(
    tags=["Password"], dependencies=[Depends(prevent_password_caching)]
)


@password.get(
    "/{device}/password/info", response_model=DeviceSecretInformation,
    response_model_exclude_none=False,
)
async def get_device_password_info(
    device: str,
    repository: PasswordRenewalTaskRepository = Depends(get_repository(PasswordRenewalTaskRepository)),
    _=Depends(ABACPermissionCheck(Device.READ)),
):
    return await get_password_info(device, repository)


@password.post(
    "/{device}/password/request", response_model=DeviceSecretValue,
    response_model_exclude_none=False,
)
async def post_device_password_request(
    device: str,
    repository: PasswordRenewalTaskRepository = Depends(get_repository(PasswordRenewalTaskRepository)),
    _=Depends(ABACPermissionCheck(Device.PASSWORD_READ)),
):
    return await request_password(device, repository)


@password.post("/{device}/password/renew", response_model=PasswordRenewalAccepted)
async def post_device_password_renew(
    device: str,
    repository: PasswordRenewalTaskRepository = Depends(get_repository(PasswordRenewalTaskRepository)),
    _=Depends(ABACPermissionCheck(Device.PASSWORD_WRITE)),
):
    await renew_password(device, repository)
    return PasswordRenewalAccepted()
