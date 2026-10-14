"""Per-device permission checks for routes whose device isn't a path parameter.

Endpoint and service routes get their device from a query parameter (`?device_id=`) or from the
database (endpoint -> device, service -> endpoint -> device). `ABACPermissionCheck` only reads
path parameters, so it can't scope them. These routes keep `ABACPermissionCheck(...,
device_path=None)` for authentication and the plain "has this permission at all" check, and
call `DeviceAccess.check(device_id)` once they know the device.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Depends

from authorization.abac_permission_check import (
    ABACPermissionCheck,
    ABACPermissionCheckResult,
    evaluate_permission,
)
from db.repos.device import DeviceRepository
from db.repos.user import UserRepository
from db.session import get_repository
from exceptions import InsufficientPermissions


async def require_device_permission(
    permission: str,
    device_id: str,
    user_id: str,
    user_repo: UserRepository,
    device_repo: DeviceRepository,
) -> None:
    """Raises InsufficientPermissions (403) unless the user holds `permission` for `device_id`.
    Mirrors ABACPermissionCheck: admins and unscoped teams pass without a device lookup."""
    user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
    if user_data is None:
        raise InsufficientPermissions("User not found", status_code=403)
    if user_data.get("is_admin"):
        return

    # Mirrors ABACPermissionCheck's team/unscoped handling so the device lookup can be skipped
    # (keep in sync).
    teams_with_permission = [
        team for team in user_data.get("teams", [])
        if any(permission in role.get("allowed_actions", []) for role in team.get("roles", []))
    ]
    if not teams_with_permission:
        raise InsufficientPermissions(f"No role grants permission '{permission}'", status_code=403)
    if any(team.get("scope") is None for team in teams_with_permission):
        return

    device_meta = await device_repo.get_device_meta_raw(device_id)
    if device_meta is None:
        raise InsufficientPermissions(f"Device '{device_id}' not found", status_code=403)
    if not evaluate_permission(permission, user_data, device_meta):
        raise InsufficientPermissions("Device is not within any assigned scope", status_code=403)


@dataclass
class DeviceAccess:
    permission: str
    user_id: str
    user_repo: UserRepository
    device_repo: DeviceRepository

    async def check(self, device_id: str) -> None:
        await require_device_permission(
            self.permission, device_id, self.user_id, self.user_repo, self.device_repo
        )


def device_access(permission: str) -> Callable[..., Awaitable[DeviceAccess]]:
    """FastAPI dependency factory: authenticates, checks that some role grants `permission`,
    and returns a DeviceAccess for the per-device check."""
    abac = ABACPermissionCheck(permission, device_path=None)

    async def _dependency(
        auth: ABACPermissionCheckResult = Depends(abac),
        user_repo: UserRepository = Depends(get_repository(UserRepository)),
        device_repo: DeviceRepository = Depends(get_repository(DeviceRepository)),
    ) -> DeviceAccess:
        return DeviceAccess(permission, auth["user_id"], user_repo, device_repo)

    return _dependency
