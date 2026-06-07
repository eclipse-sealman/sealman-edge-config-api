from fastapi import Depends, Request
import logging
from typing import Any, Callable, Dict, List, Optional, TypedDict

from auth import get_current_user, validate_jwt
from db.repos.device import DeviceRepository
from db.repos.user import UserRepository
from db.session import get_repository
from exceptions import InsufficientPermissions


logger = logging.getLogger("EdgeConfigAPI")


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
    def __init__(self, permission: str, device_path: str = "device"):
        self.permission = permission
        self.device_path = device_path

    async def __call__(
        self,
        request: Request,
        auth_context: dict = Depends(validate_jwt),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
        device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    ) -> ABACPermissionCheckResult:
        device_id = request.path_params.get(self.device_path) if self.device_path else None
        user_id = auth_context.get("oid") or auth_context.get("sub")
        user_name = get_current_user(auth_context)

        logger.debug(f"ABAC permission check: {self.permission} for device {device_id} by user {user_name}")

        if not user_id:
            raise InsufficientPermissions("User identifier missing from token", status_code=403)

        user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
        if user_data is None:
            raise InsufficientPermissions("User not found", status_code=403)

        device_meta = None

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
            if device_id is not None and not any(team.get("scope") is None for team in teams_with_permission):
                device_meta = await device_repo.get_device_meta_raw(device_id)
                if device_meta is None:
                    raise InsufficientPermissions(
                        f"Device '{device_id}' not found", status_code=403
                    )

        if not evaluate_permission(self.permission, user_data, device_meta):
            raise InsufficientPermissions(
                "Device is not within any assigned scope", status_code=403
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
