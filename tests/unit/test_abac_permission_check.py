import pytest
from unittest.mock import AsyncMock, MagicMock

from authorization.abac_permission_check import ABACPermissionCheck, ABACDeviceListFilter, _matches_scope
from exceptions import InsufficientPermissions


# =============================================================================
# Tests for _matches_scope
# =============================================================================


class TestMatchesScope:
    """Unit tests for the scope attribute matching function."""

    def test_empty_scope_attr_always_matches(self):
        assert _matches_scope({}, "ALL", {"countryCode": "DEU"}) is True
        assert _matches_scope({}, "ANY", {"countryCode": "DEU"}) is True

    # --- ALL rule ---

    def test_all_single_attr_match(self):
        assert _matches_scope(
            {"countryCode": "DEU"}, "ALL", {"countryCode": "DEU", "customer": "Lidl"}
        ) is True

    def test_all_single_attr_no_match(self):
        assert _matches_scope(
            {"countryCode": "DEU"}, "ALL", {"countryCode": "USA"}
        ) is False

    def test_all_multiple_attrs_all_match(self):
        assert _matches_scope(
            {"countryCode": "DEU", "customer": "Lidl"},
            "ALL",
            {"countryCode": "DEU", "customer": "Lidl", "city": "Munich"},
        ) is True

    def test_all_multiple_attrs_partial_match(self):
        assert _matches_scope(
            {"countryCode": "DEU", "customer": "Lidl"},
            "ALL",
            {"countryCode": "DEU", "customer": "Aldi"},
        ) is False

    def test_all_attr_missing_in_device_meta(self):
        assert _matches_scope(
            {"countryCode": "DEU"}, "ALL", {"customer": "Lidl"}
        ) is False

    def test_all_list_value_match(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"]},
            "ALL",
            {"countryCode": "DEU"},
        ) is True

    def test_all_list_value_match_second(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"]},
            "ALL",
            {"countryCode": "RS"},
        ) is True

    def test_all_list_value_no_match(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"]},
            "ALL",
            {"countryCode": "USA"},
        ) is False

    def test_all_mixed_scalar_and_list(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"], "customer": "Lidl"},
            "ALL",
            {"countryCode": "DEU", "customer": "Lidl"},
        ) is True

    def test_all_mixed_scalar_and_list_partial_fail(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"], "customer": "Lidl"},
            "ALL",
            {"countryCode": "DEU", "customer": "Aldi"},
        ) is False

    # --- ANY rule ---

    def test_any_single_attr_match(self):
        assert _matches_scope(
            {"countryCode": "DEU"}, "ANY", {"countryCode": "DEU"}
        ) is True

    def test_any_single_attr_no_match(self):
        assert _matches_scope(
            {"countryCode": "DEU"}, "ANY", {"countryCode": "USA"}
        ) is False

    def test_any_multiple_attrs_one_matches(self):
        # countryCode matches, customer does not — ANY should pass
        assert _matches_scope(
            {"countryCode": "DEU", "customer": "Lidl"},
            "ANY",
            {"countryCode": "DEU", "customer": "Aldi"},
        ) is True

    def test_any_multiple_attrs_none_match(self):
        assert _matches_scope(
            {"countryCode": "DEU", "customer": "Lidl"},
            "ANY",
            {"countryCode": "USA", "customer": "Aldi"},
        ) is False

    def test_any_list_value_match(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"]},
            "ANY",
            {"countryCode": "RS"},
        ) is True

    def test_any_list_value_no_match(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"]},
            "ANY",
            {"countryCode": "USA"},
        ) is False

    def test_any_mixed_only_list_matches(self):
        assert _matches_scope(
            {"countryCode": ["DEU", "RS"], "customer": "Lidl"},
            "ANY",
            {"countryCode": "RS", "customer": "Aldi"},
        ) is True

    # --- Edge cases ---

    def test_device_meta_empty(self):
        assert _matches_scope({"countryCode": "DEU"}, "ALL", {}) is False
        assert _matches_scope({"countryCode": "DEU"}, "ANY", {}) is False

    def test_device_meta_value_none(self):
        assert _matches_scope(
            {"countryCode": "DEU"}, "ALL", {"countryCode": None}
        ) is False


# =============================================================================
# Tests for ABACPermissionCheck
# =============================================================================


class TestABACPermissionCheck:
    """Unit tests for the ABACPermissionCheck callable."""

    def _make_request(self, path_params=None):
        request = MagicMock()
        request.path_params = path_params or {}
        return request

    def _make_user_data(self, is_admin=False, teams=None):
        return {
            "id": "user-oid-123",
            "is_admin": is_admin,
            "teams": teams or [],
        }

    def _make_team(self, actions, scope=None):
        return {
            "id": "team-1",
            "name": "Team A",
            "scope": scope,
            "roles": [
                {
                    "id": "role-1",
                    "name": "Editor",
                    "allowed_actions": actions,
                }
            ],
        }

    @pytest.fixture
    def user_repo(self):
        return AsyncMock()

    @pytest.fixture
    def device_repo(self):
        return AsyncMock()

    @pytest.fixture
    def auth_context(self):
        return {
            "oid": "user-oid-123",
            "sub": "user-sub-123",
            "name": "Test User",
            "roles": ["user.editor"],
        }

    @pytest.mark.asyncio
    async def test_admin_bypass(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(is_admin=True)

        result = await checker(request, auth_context, user_repo, device_repo)

        assert result["user_name"] == "Test User"
        assert result["device_id"] == "device-1"
        device_repo.get_device_meta_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_not_found_raises_403(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request()

        user_repo.get_teams_with_roles_and_scopes.return_value = None

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(request, auth_context, user_repo, device_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_teams_raises_403(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request()

        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(teams=[])

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(request, auth_context, user_repo, device_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_role_with_permission_raises_403(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request()

        team = self._make_team(actions=["device.deployment.write"])
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(request, auth_context, user_repo, device_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_permission_granted_no_device_id(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read", device_path="")
        request = self._make_request()

        team = self._make_team(actions=["device.read"])
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )

        result = await checker(request, auth_context, user_repo, device_repo)

        assert result["permission"] == "device.read"
        assert result["device_id"] is None
        device_repo.get_device_meta_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_scope_means_unrestricted_access(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        team = self._make_team(actions=["device.read"], scope=None)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )

        result = await checker(request, auth_context, user_repo, device_repo)

        assert result["device_id"] == "device-1"
        device_repo.get_device_meta_raw.assert_not_called()

    @pytest.mark.asyncio
    async def test_scope_matches_device_meta(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        scope = {"attr": {"countryCode": "DEU"}, "access_rule": "ALL"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )
        device_repo.get_device_meta_raw.return_value = {"countryCode": "DEU", "customer": "Lidl"}

        result = await checker(request, auth_context, user_repo, device_repo)

        assert result["device_id"] == "device-1"

    @pytest.mark.asyncio
    async def test_scope_does_not_match_raises_403(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        scope = {"attr": {"countryCode": "DEU"}, "access_rule": "ALL"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )
        device_repo.get_device_meta_raw.return_value = {"countryCode": "USA"}

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(request, auth_context, user_repo, device_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_device_not_found_raises_403(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "nonexistent"})

        scope = {"attr": {"countryCode": "DEU"}, "access_rule": "ALL"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )
        device_repo.get_device_meta_raw.return_value = None

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(request, auth_context, user_repo, device_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_multiple_teams_one_scope_matches(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        team_no_match = self._make_team(
            actions=["device.read"],
            scope={"attr": {"countryCode": "USA"}, "access_rule": "ALL"},
        )
        team_match = self._make_team(
            actions=["device.read"],
            scope={"attr": {"countryCode": "DEU"}, "access_rule": "ALL"},
        )
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team_no_match, team_match]
        )
        device_repo.get_device_meta_raw.return_value = {"countryCode": "DEU"}

        result = await checker(request, auth_context, user_repo, device_repo)
        assert result["device_id"] == "device-1"

    @pytest.mark.asyncio
    async def test_any_rule_partial_match_grants_access(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        scope = {"attr": {"countryCode": "DEU", "customer": "Lidl"}, "access_rule": "ANY"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )
        # Only countryCode matches, customer does not — ANY rule passes
        device_repo.get_device_meta_raw.return_value = {"countryCode": "DEU", "customer": "Aldi"}

        result = await checker(request, auth_context, user_repo, device_repo)
        assert result["device_id"] == "device-1"

    @pytest.mark.asyncio
    async def test_list_scope_value_matching(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read")
        request = self._make_request({"device": "device-1"})

        scope = {"attr": {"countryCode": ["DEU", "RS"]}, "access_rule": "ALL"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )
        device_repo.get_device_meta_raw.return_value = {"countryCode": "RS"}

        result = await checker(request, auth_context, user_repo, device_repo)
        assert result["device_id"] == "device-1"

    @pytest.mark.asyncio
    async def test_device_path_param_extraction(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read", device_path="device_id")
        request = self._make_request({"device_id": "my-device"})

        team = self._make_team(actions=["device.read"], scope=None)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team]
        )

        result = await checker(request, auth_context, user_repo, device_repo)
        assert result["device_id"] == "my-device"


# =============================================================================
# Tests for ABACDeviceListFilter
# =============================================================================


class TestABACDeviceListFilter:
    """Unit tests for the ABACDeviceListFilter callable."""

    def _make_user_data(self, is_admin=False, teams=None):
        return {
            "id": "user-oid-123",
            "is_admin": is_admin,
            "teams": teams or [],
        }

    def _make_team(self, actions, scope=None):
        return {
            "id": "team-1",
            "name": "Team A",
            "scope": scope,
            "roles": [
                {
                    "id": "role-1",
                    "name": "Editor",
                    "allowed_actions": actions,
                }
            ],
        }

    @pytest.fixture
    def user_repo(self):
        return AsyncMock()

    @pytest.fixture
    def auth_context(self):
        return {
            "oid": "user-oid-123",
            "sub": "user-sub-123",
            "name": "Test User",
            "roles": ["user.editor"],
        }

    @pytest.mark.asyncio
    async def test_admin_returns_unrestricted(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(is_admin=True)

        result = await checker(auth_context, user_repo)

        assert result["is_unrestricted"] is True
        assert result["filter_device"]({"countryCode": "anything"}) is True

    @pytest.mark.asyncio
    async def test_user_not_found_raises_403(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        user_repo.get_teams_with_roles_and_scopes.return_value = None

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(auth_context, user_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_teams_raises_403(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(teams=[])

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(auth_context, user_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_permission_raises_403(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        team = self._make_team(actions=["device.deployment.write"])
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(teams=[team])

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(auth_context, user_repo)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_scope_means_unrestricted(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        team = self._make_team(actions=["device.read"], scope=None)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(teams=[team])

        result = await checker(auth_context, user_repo)

        assert result["is_unrestricted"] is True
        assert result["filter_device"]({"anything": "value"}) is True

    @pytest.mark.asyncio
    async def test_scope_filter_matches(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        scope = {"attr": {"countryCode": "DEU"}, "access_rule": "ALL"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(teams=[team])

        result = await checker(auth_context, user_repo)

        assert result["is_unrestricted"] is False
        assert result["filter_device"]({"countryCode": "DEU"}) is True
        assert result["filter_device"]({"countryCode": "USA"}) is False

    @pytest.mark.asyncio
    async def test_multiple_scopes_any_match_passes(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        team1 = self._make_team(
            actions=["device.read"],
            scope={"attr": {"countryCode": "DEU"}, "access_rule": "ALL"},
        )
        team2 = self._make_team(
            actions=["device.read"],
            scope={"attr": {"countryCode": "RS"}, "access_rule": "ALL"},
        )
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(
            teams=[team1, team2]
        )

        result = await checker(auth_context, user_repo)

        assert result["filter_device"]({"countryCode": "DEU"}) is True
        assert result["filter_device"]({"countryCode": "RS"}) is True
        assert result["filter_device"]({"countryCode": "USA"}) is False

    @pytest.mark.asyncio
    async def test_filter_with_list_scope_value(self, user_repo, auth_context):
        checker = ABACDeviceListFilter("device.read")
        scope = {"attr": {"countryCode": ["DEU", "RS"]}, "access_rule": "ALL"}
        team = self._make_team(actions=["device.read"], scope=scope)
        user_repo.get_teams_with_roles_and_scopes.return_value = self._make_user_data(teams=[team])

        result = await checker(auth_context, user_repo)

        assert result["filter_device"]({"countryCode": "DEU"}) is True
        assert result["filter_device"]({"countryCode": "RS"}) is True
        assert result["filter_device"]({"countryCode": "USA"}) is False
