from fastapi import Depends, HTTPException
from auth import RBACPermissionChecker, validate_jwt
from authorization.permission_types import Device, Platform
from exceptions import UserNotFound
from routers.auth.schemas import UserPermissions
from routers.base_api_router import BaseAPIRouter
from constants import AUTHORIZATION_API_PLATFORM_NAME


auth = BaseAPIRouter()


@auth.get(
    "/auth/permissions/{resource_type}", response_model=UserPermissions, tags=["Auth"]
)
async def get_permissions(
    resource_type: str,
    resource_id: str = None,
    auth_context: dict = Depends(validate_jwt),
):

    if resource_type == "platform" and resource_id is None:
        resource_id = AUTHORIZATION_API_PLATFORM_NAME

    if resource_id is None:
        raise HTTPException(status_code=400, detail="resource_id is required")

    user_id = auth_context.get("oid") or auth_context.get("sub")
    if (user_id is None):
        raise UserNotFound(status_code=403)

    # In RBAC mode, derive permissions from the user's assigned roles.
    if resource_type.lower() == "platform":
        permission_resource_type = Platform
    elif resource_type.lower() == "device":
        permission_resource_type = Device
    else:
        raise HTTPException(status_code=400, detail="Invalid resource type")

    assigned_permissions = RBACPermissionChecker.get_assigned_permissions(auth_context)
    assigned_permission_map = {perm: True for perm in assigned_permissions}
    available_permissions = {
        value
        for attr in dir(permission_resource_type)
        if not attr.startswith("__")
        for value in [getattr(permission_resource_type, attr)]
        if isinstance(value, str)
    }
    user_permissions = [
        permission for permission in available_permissions if permission in assigned_permission_map
    ]

    return UserPermissions(
        ResourceType=resource_type, ResourceId=resource_id, Permissions=user_permissions
    )