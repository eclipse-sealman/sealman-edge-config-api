from fastapi import Depends, Request
import logging
from typing import Any, Dict, List, Optional

from auth import get_current_user, validate_jwt
from db.repos.device import DeviceRepository
from db.repos.user import UserRepository
from db.session import get_repository
from exceptions import InsufficientPermissions


logger = logging.getLogger("EdgeConfigAPI")


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
    ) -> dict:
        device_id = request.path_params.get(self.device_path) if self.device_path else None
        user_id = auth_context.get("oid") or auth_context.get("sub")
        user_name = get_current_user(auth_context)

        logger.info(f"ABAC permission check: {self.permission} for device {device_id} by user {user_name}")

        if not user_id:
            raise InsufficientPermissions("User identifier missing from token", status_code=403)

        # Fetch user with teams, roles, and scopes
        user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)

        if user_data is None:
            raise InsufficientPermissions("User not found", status_code=403)

        # Admin bypass
        if user_data.get("is_admin"):
            return self._build_result(user_name, user_id, device_id)

        teams = user_data.get("teams", [])
        if not teams:
            raise InsufficientPermissions(
                "User has no team assignments", status_code=403
            )

        # Find teams where at least one role has the required permission
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

        # If no device_id — permission is granted (no scope filtering needed)
        if device_id is None:
            return self._build_result(user_name, user_id, device_id)

        # Device-level scope check
        # If any team with permission has no scope → unrestricted access
        if any(team.get("scope") is None for team in teams_with_permission):
            return self._build_result(user_name, user_id, device_id)

        # Fetch raw device metadata for scope evaluation
        device_meta = await device_repo.get_device_meta_raw(device_id)

        if device_meta is None:
            raise InsufficientPermissions(
                f"Device '{device_id}' not found", status_code=403
            )

        # Evaluate each team's scope against device metadata
        for team in teams_with_permission:
            scope = team["scope"]
            if _matches_scope(scope["attr"], scope["access_rule"], device_meta):
                return self._build_result(user_name, user_id, device_id)

        raise InsufficientPermissions(
            "Device is not within any assigned scope", status_code=403
        )

    def _build_result(self, user_name: str, user_id: Optional[str], device_id: Optional[str]) -> dict:
        return {
            "user_name": user_name,
            "user_id": user_id,
            "permission": self.permission,
            "device_id": device_id,
        }
