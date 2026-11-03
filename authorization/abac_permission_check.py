from fastapi import Depends, Request
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from auth import get_current_user, validate_jwt
from db.merge import patch_data
from db.repos.device import DeviceRepository
from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from db.repos.user import UserRepository
from db.session import get_repository
from authorization.permission_types import get_all_permissions
from exceptions import APIError, InsufficientPermissions


logger = logging.getLogger("EdgeConfigAPI")

_GLOBAL_PERMISSIONS = frozenset(p["name"] for p in get_all_permissions() if p["is_global"])


class ABACBaseResult(TypedDict):
    user_id: str
    user_name: str
    permission: str


class ABACPermissionCheckResult(ABACBaseResult):
    device_id: Optional[str]


class ABACDeviceListFilterResult(ABACBaseResult):
    is_unrestricted: bool
    filter_device: Callable[[Dict[str, Any]], bool]


def _key_matches(key: str, scope_value: Any, device_meta: Dict[str, Any]) -> bool:
    device_value = device_meta.get(key)
    if device_value is None:
        return False
    if isinstance(scope_value, list):
        return device_value in scope_value
    return device_value == scope_value


def _matches_scope(scope_attr: Dict[str, Any], access_rule: str, device_meta: Dict[str, Any]) -> bool:
    """
    Evaluate whether device metadata satisfies the scope's attribute constraints.

    - ALL: every attribute key in scope must match in device_meta
    - ANY: at least one attribute key must match

    For each key:
      - If scope value is a list: device_meta[key] must be contained in that list
      - If scope value is a scalar: device_meta[key] must equal the scope value
    """
    if not scope_attr:
        return True

    if access_rule == "ALL":
        return all(_key_matches(k, v, device_meta) for k, v in scope_attr.items())
    else:  # ANY
        return any(_key_matches(k, v, device_meta) for k, v in scope_attr.items())


def evaluate_permission(
    permission: str,
    user_data: dict,
    device_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Evaluates whether a user has the given permission.

    Pure function operating on pre-fetched data. Use it directly from request
    handlers to check multiple permissions without instantiating ABACPermissionCheck
    or catching exceptions.

    Args:
        permission: The permission string to evaluate.
        user_data: Result of UserRepository.get_teams_with_roles_and_scopes().
        device_meta: Device metadata for scope evaluation. Pass None to skip
                     scope filtering (e.g. for platform-level permission checks).

    Returns:
        True if the user has the permission, False otherwise.
    """
    if user_data.get("is_admin"):
        return True

    teams = user_data.get("teams", [])
    teams_with_permission = [
        team for team in teams
        if any(
            permission in role.get("allowed_actions", [])
            for role in team.get("roles", [])
        )
    ]

    if not teams_with_permission:
        return False

    # No device metadata provided — skip scope filtering
    if device_meta is None:
        return True

    # Unrestricted if any relevant team has no scope constraint
    if any(team.get("scope") is None for team in teams_with_permission):
        return True

    # Evaluate scope constraints against device metadata
    return any(
        _matches_scope(team["scope"]["attr"], team["scope"]["access_rule"], device_meta)
        for team in teams_with_permission
    )


class ABACPermissionCheck:
    def __init__(self, permission: str, device_path: str = "device", device_in: str = "path"):
        self.permission = permission
        self.device_path = device_path
        self.device_in = device_in

    async def __call__(
        self,
        request: Request,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
        device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    ) -> ABACPermissionCheckResult:
        device_id = None
        if self.device_path:
            if self.device_in == "query":
                device_id = request.query_params.get(self.device_path)
            else:
                device_id = request.path_params.get(self.device_path)

        # A device permission on a route whose path lacks the expected parameter would
        # silently skip scope evaluation, so treat it as a misconfiguration and deny.
        if self.device_path and not device_id and self.permission not in _GLOBAL_PERMISSIONS:
            logger.error(
                f"ABAC misconfiguration: {self.device_in} parameter '{self.device_path}' not found "
                f"for permission '{self.permission}' on {request.url.path}"
            )
            raise InsufficientPermissions("Device identifier could not be resolved", status_code=403)

        return await self._authorize(auth_context, user_repo, device_repo, device_id)

    async def _authorize(
        self,
        auth_context: dict,
        user_repo: UserRepository,
        device_repo: DeviceRepository,
        device_id: Optional[str],
        require_device: bool = False,
        resulting_meta: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        resolve_device: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
    ) -> ABACPermissionCheckResult:
        """
        Single role + scope evaluation shared by all device-aware checks. With
        ``require_device`` a scope-restricted caller is denied when ``device_id`` is None.
        ``resulting_meta`` maps the stored metadata to the metadata after the operation, which
        must also be within scope (so a write cannot move a device out of the caller's scope).
        """
        user_id = auth_context.get("oid") or auth_context.get("sub")
        user_name = get_current_user(auth_context)

        logger.debug(f"ABAC permission check: {self.permission} for device {device_id} by user {user_name}")

        if not user_id:
            raise InsufficientPermissions("User identifier missing from token", status_code=403)

        user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
        if user_data is None:
            raise InsufficientPermissions("User not found", status_code=403)

        device_meta = None
        scope_required = False

        if not user_data.get("is_admin"):
            teams = user_data.get("teams", [])
            if not teams:
                raise InsufficientPermissions("User has no team assignments", status_code=403)

            teams_with_permission = [
                team for team in teams
                if any(
                    self.permission in role.get("allowed_actions", [])
                    for role in team.get("roles", [])
                )
            ]
            if not teams_with_permission:
                raise InsufficientPermissions(
                    f"No role grants permission '{self.permission}'", status_code=403
                )

            # Fetch device metadata only when scope filtering is required
            scope_required = not any(team.get("scope") is None for team in teams_with_permission)

        if resolve_device is not None:
            device_id = await resolve_device()
        if scope_required and device_id is None and require_device:
            raise InsufficientPermissions("Device identifier could not be resolved", status_code=403)
        if device_id is not None and scope_required:
            device_meta = await device_repo.get_device_meta_raw(device_id)
            if device_meta is None:
                raise InsufficientPermissions(
                    f"Device '{device_id}' not found", status_code=403
                )

        if not evaluate_permission(self.permission, user_data, device_meta):
            raise InsufficientPermissions(
                "Device is not within any assigned scope", status_code=403
            )

        if resulting_meta is not None and device_meta is not None:
            if not evaluate_permission(self.permission, user_data, resulting_meta(device_meta)):
                raise InsufficientPermissions(
                    "Resulting device metadata would be outside the assigned scope", status_code=403
                )

        return self._build_result(user_name, user_id, device_id)

    def _build_result(
        self, user_name: str, user_id: str, device_id: Optional[str]
    ) -> ABACPermissionCheckResult:
        return {
            "user_name": user_name,
            "user_id": user_id,
            "permission": self.permission,
            "device_id": device_id,
        }


class ABACMetadataPatchCheck(ABACPermissionCheck):
    """
    Like ABACPermissionCheck, but for a metadata patch: besides the device being in scope
    now, the metadata after applying the patch body must still be in scope.
    """

    async def __call__(
        self,
        request: Request,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
        device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    ) -> ABACPermissionCheckResult:
        device_id = request.path_params.get(self.device_path)
        if device_id is None:
            raise InsufficientPermissions("Device identifier could not be resolved", status_code=403)

        try:
            body = await request.json()
        except ValueError:
            raise APIError("Request body must be valid JSON", 400)
        if not isinstance(body, dict):
            raise APIError("Request body must be a JSON object", 400)

        return await self._authorize(
            auth_context,
            user_repo,
            device_repo,
            device_id,
            resulting_meta=lambda current: patch_data(current, body),
        )


class ABACResolvedDeviceCheck(ABACPermissionCheck):
    """
    ABAC dependency for routes that identify a device indirectly. The owning device is
    resolved from a path or query parameter, then the shared role + scope evaluation
    of ``ABACPermissionCheck`` runs on it.

    ``owner`` selects what the parameter identifies:
      - "device":   the parameter is the device ID itself (e.g. ``?device_id=``)
      - "endpoint": the parameter is an endpoint ID (endpoint -> device)
      - "service":  the parameter is a service ID (service -> endpoint -> device)

    The action is checked before ownership lookup. Missing endpoints/services return 404;
    scope-restricted callers get 403 when the owning device is outside their scope.
    """

    def __init__(self, permission: str, owner: str, param: str):
        if owner not in ("device", "endpoint", "service"):
            raise ValueError(f"Unsupported owner type '{owner}'")
        super().__init__(permission, device_path=None)
        self.owner = owner
        self.param = param

    async def _resolve_device_id(
        self,
        value: str,
        endpoint_repo: EndpointRepository,
        service_repo: ServiceRepository,
    ) -> Optional[str]:
        if self.owner == "device":
            return value
        if self.owner == "endpoint":
            return await endpoint_repo.get_endpoint_device_id(value)
        return await service_repo.get_service_device_id(value)

    async def __call__(
        self,
        request: Request,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
        device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
        endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
        service_repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
    ) -> ABACPermissionCheckResult:
        async def resolve_device() -> Optional[str]:
            value = request.path_params.get(self.param) or request.query_params.get(self.param)
            if not value:
                return None
            device_id = await self._resolve_device_id(value, endpoint_repo, service_repo)
            if device_id is None and self.owner != "device":
                raise APIError(f"{self.owner.capitalize()} '{value}' not found", 404)
            return device_id

        return await self._authorize(
            auth_context, user_repo, device_repo, None,
            require_device=True, resolve_device=resolve_device,
        )


class ABACDeviceCreateCheck:
    """
    ABAC dependency for creating a device. The device does not exist yet, so the
    scope is evaluated against the proposed ``meta`` from the request body instead
    of stored metadata. Requires a role granting the permission and, unless the
    caller is an admin or has an unscoped team, a scope matching the proposed meta.
    """

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(
        self,
        request: Request,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
    ) -> ABACPermissionCheckResult:
        user_id = auth_context.get("oid") or auth_context.get("sub")
        user_name = get_current_user(auth_context)

        if not user_id:
            raise InsufficientPermissions("User identifier missing from token", status_code=403)

        user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
        if user_data is None:
            raise InsufficientPermissions("User not found", status_code=403)

        if not user_data.get("is_admin"):
            teams = user_data.get("teams", [])
            if not teams:
                raise InsufficientPermissions("User has no team assignments", status_code=403)

            if not evaluate_permission(self.permission, user_data):
                raise InsufficientPermissions(
                    f"No role grants permission '{self.permission}'", status_code=403
                )

            try:
                body = await request.json()
            except ValueError:
                raise APIError("Request body must be valid JSON", 400)
            if not isinstance(body, dict):
                raise APIError("Request body must be a JSON object", 400)

            meta = body.get("meta")
            if meta is None:
                meta = {}
            if not isinstance(meta, dict):
                raise APIError("'meta' must be a JSON object", 400)

            if not evaluate_permission(self.permission, user_data, meta):
                raise InsufficientPermissions(
                    "Device metadata is not within any assigned scope", status_code=403
                )

        return {
            "user_name": user_name,
            "user_id": user_id,
            "permission": self.permission,
            "device_id": request.path_params.get("device_id"),
        }


class ABACAnyPermissionCheck:
    """
    ABAC dependency for global (non device-scoped) routes that any one of several
    actions may unlock, e.g. a resource that both a platform reader and a device
    creator need. Admins pass; everyone else needs a role granting at least one action.
    """

    def __init__(self, *permissions: str):
        if not permissions:
            raise ValueError("At least one permission is required")
        self.permissions = permissions

    async def __call__(
        self,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
    ) -> ABACBaseResult:
        user_id = auth_context.get("oid") or auth_context.get("sub")
        user_name = get_current_user(auth_context)

        if not user_id:
            raise InsufficientPermissions("User identifier missing from token", status_code=403)

        user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
        if user_data is None:
            raise InsufficientPermissions("User not found", status_code=403)

        granted = self.permissions[0]
        if not user_data.get("is_admin"):
            if not user_data.get("teams"):
                raise InsufficientPermissions("User has no team assignments", status_code=403)
            granted = next((p for p in self.permissions if evaluate_permission(p, user_data)), None)
            if granted is None:
                raise InsufficientPermissions(
                    f"No role grants any of: {', '.join(self.permissions)}", status_code=403
                )

        return {"user_name": user_name, "user_id": user_id, "permission": granted}


class ABACDeviceListFilter:
    """
    ABAC dependency for list endpoints that returns a filter function
    instead of checking a single device. The filter evaluates each device's
    raw metadata against the user's team scopes.

    Returns a dict with:
      - "user_name", "user_id", "permission"
      - "is_unrestricted": True if no scope filtering is needed
      - "filter_device": callable (device_meta: dict) -> bool
    """

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(
        self,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
    ) -> ABACDeviceListFilterResult:
        user_id = auth_context.get("oid") or auth_context.get("sub")
        user_name = get_current_user(auth_context)

        if not user_id:
            raise InsufficientPermissions("User identifier missing from token", status_code=403)

        user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)

        if user_data is None:
            raise InsufficientPermissions("User not found", status_code=403)

        # Admin bypass — unrestricted access
        if user_data.get("is_admin"):
            return self._build_result(user_name, user_id, is_unrestricted=True, scopes=[])

        teams = user_data.get("teams", [])
        if not teams:
            raise InsufficientPermissions("User has no team assignments", status_code=403)

        teams_with_permission = [
            team for team in teams
            if any(
                self.permission in role.get("allowed_actions", [])
                for role in team.get("roles", [])
            )
        ]

        if not teams_with_permission:
            raise InsufficientPermissions(
                f"No role grants permission '{self.permission}'", status_code=403
            )

        # If any team has no scope → unrestricted
        if any(team.get("scope") is None for team in teams_with_permission):
            return self._build_result(user_name, user_id, is_unrestricted=True, scopes=[])

        scopes = [team["scope"] for team in teams_with_permission]
        return self._build_result(user_name, user_id, is_unrestricted=False, scopes=scopes)

    def _build_result(
        self, user_name: str, user_id: str, is_unrestricted: bool, scopes: List[Dict[str, Any]]
    ) -> ABACDeviceListFilterResult:
        def filter_device(device_meta: Dict[str, Any]) -> bool:
            if is_unrestricted:
                return True
            return any(
                _matches_scope(scope["attr"], scope["access_rule"], device_meta)
                for scope in scopes
            )

        return {
            "user_name": user_name,
            "user_id": user_id,
            "permission": self.permission,
            "is_unrestricted": is_unrestricted,
            "filter_device": filter_device,
        }
