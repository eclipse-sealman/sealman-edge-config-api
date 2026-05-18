from fastapi import Depends, Request

from auth import RBACPermissionChecker, get_current_user, validate_jwt


class PermissionCheckBase:
    def __init__(self, permission: str, resource_type: str):
        self.permission = permission
        self.resource_type = resource_type

    async def _check_permission_on_resource(self, auth_context: dict, resource_id: str) -> dict:
        await RBACPermissionChecker([self.permission])(auth_context)
        return {
            "user_name": get_current_user(auth_context),
            "user_id": auth_context.get("oid") or auth_context.get("sub"),
            "resource_type": self.resource_type,
            "permission": self.permission,
            "resource_id": resource_id
        }

# Makes the permissions check on the user given the resource_type and resource_id
class PermissionCheck(PermissionCheckBase):
    def __init__(self, permission: str, resource_type: str, resource_id: str):
        super().__init__(permission, resource_type)
        self.resource_id = resource_id

    async def __call__(self, auth_context: dict = Depends(validate_jwt)) -> dict:
        return await super()._check_permission_on_resource(auth_context, self.resource_id)


# Extracts the resource id from the path parameter using the given path_param.
# Then checks whether the current user has the given permission on the given resource type and the extracted id
class PathParamPermissionCheck(PermissionCheckBase):
    def __init__(self, permission: str, resource_type: str, path_param: str):
        super().__init__(permission, resource_type)
        self.path_param = path_param

    async def __call__(self, request: Request, auth_context: dict = Depends(validate_jwt)) -> dict:
        resource_id = request.path_params.get(self.path_param)
        return await super()._check_permission_on_resource(auth_context, resource_id)


# Extracts the resource id from the query parameter using the given query_param.
# Then checks whether the current user has the given permission on the given resource type and the extracted id
class QueryParamPermissionCheck(PermissionCheckBase):
    def __init__(self, permission: str, resource_type: str, query_param: str):
        super().__init__(permission, resource_type)
        self.query_param = query_param

    async def __call__(self, request: Request, auth_context: dict = Depends(validate_jwt)) -> dict:
        resource_id = request.query_params.get(self.query_param)
        return await super()._check_permission_on_resource(auth_context, resource_id)


# Returns a list of entities the user has the given permission on
class EntityLookup(PermissionCheckBase):

    async def __call__(self, auth_context: dict = Depends(validate_jwt)) -> dict:
        await RBACPermissionChecker([self.permission])(auth_context)
        return dict()

        
