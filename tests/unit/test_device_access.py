from unittest.mock import AsyncMock

import pytest

from authorization.device_access import DeviceAccess, require_device_permission
from exceptions import InsufficientPermissions

PERM = "device.endpoint.read"
EU_SCOPE = {"attr": {"region": "EU"}, "access_rule": "ALL"}


def _team(actions, scope=None):
    return {"roles": [{"allowed_actions": actions}], "scope": scope}


def _repos(user_data, device_meta=None):
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = user_data
    device_repo = AsyncMock()
    device_repo.get_device_meta_raw.return_value = device_meta
    return user_repo, device_repo


class TestRequireDevicePermission:
    async def test_admin_passes_without_loading_the_device(self):
        user_repo, device_repo = _repos({"is_admin": True, "teams": []})
        await require_device_permission(PERM, "dev-1", "u1", user_repo, device_repo)
        device_repo.get_device_meta_raw.assert_not_called()

    async def test_unscoped_team_passes_without_loading_the_device(self):
        user_repo, device_repo = _repos({"is_admin": False, "teams": [_team([PERM])]})
        await require_device_permission(PERM, "dev-1", "u1", user_repo, device_repo)
        device_repo.get_device_meta_raw.assert_not_called()

    async def test_device_in_scope_passes(self):
        user_repo, device_repo = _repos(
            {"is_admin": False, "teams": [_team([PERM], EU_SCOPE)]}, {"region": "EU"}
        )
        await require_device_permission(PERM, "dev-1", "u1", user_repo, device_repo)
        device_repo.get_device_meta_raw.assert_awaited_once_with("dev-1")

    async def test_device_out_of_scope_is_403(self):
        user_repo, device_repo = _repos(
            {"is_admin": False, "teams": [_team([PERM], EU_SCOPE)]}, {"region": "US"}
        )
        with pytest.raises(InsufficientPermissions) as exc_info:
            await require_device_permission(PERM, "dev-1", "u1", user_repo, device_repo)
        assert exc_info.value.status_code == 403

    async def test_missing_permission_is_403(self):
        user_repo, device_repo = _repos({"is_admin": False, "teams": [_team(["device.read"])]})
        with pytest.raises(InsufficientPermissions) as exc_info:
            await require_device_permission(PERM, "dev-1", "u1", user_repo, device_repo)
        assert exc_info.value.status_code == 403

    async def test_unknown_device_is_403_for_a_scoped_user(self):
        user_repo, device_repo = _repos(
            {"is_admin": False, "teams": [_team([PERM], EU_SCOPE)]}, None
        )
        with pytest.raises(InsufficientPermissions) as exc_info:
            await require_device_permission(PERM, "ghost", "u1", user_repo, device_repo)
        assert exc_info.value.status_code == 403

    async def test_unknown_user_is_403(self):
        user_repo, device_repo = _repos(None)
        with pytest.raises(InsufficientPermissions):
            await require_device_permission(PERM, "dev-1", "u1", user_repo, device_repo)


class TestDeviceAccess:
    async def test_check_uses_the_bound_permission_and_user(self):
        user_repo, device_repo = _repos(
            {"is_admin": False, "teams": [_team(["device.endpoint.write"], EU_SCOPE)]}, {"region": "EU"}
        )
        access = DeviceAccess("device.endpoint.write", "u1", user_repo, device_repo)
        await access.check("dev-1")
        user_repo.get_teams_with_roles_and_scopes.assert_awaited_once_with("u1")
