import pytest
from unittest.mock import AsyncMock, MagicMock

from authorization.abac_permission_check import ABACPermissionCheck, ABACDeviceListFilter, _matches_scope
from exceptions import APIError, InsufficientPermissions


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

    def _make_request(self, path_params=None, query_params=None):
        request = MagicMock()
        request.path_params = path_params or {}
        request.query_params = MagicMock()
        request.query_params.get = lambda key, default=None: (query_params or {}).get(key, default)
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

    @pytest.mark.asyncio
    async def test_device_query_param_extraction(self, user_repo, device_repo, auth_context):
        checker = ABACPermissionCheck("device.read", device_path="device_id", device_in="query")
        request = self._make_request(query_params={"device_id": "my-device"})

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


# =============================================================================
# Tests for fail-closed device resolution
# =============================================================================


class TestABACPermissionCheckFailClosed:
    @pytest.mark.asyncio
    async def test_missing_device_path_param_raises_403_even_for_admin(self):
        checker = ABACPermissionCheck("device.read")
        request = MagicMock()
        request.path_params = {"device_id": "device-1"}  # route uses device_id, checker expects "device"
        user_repo = AsyncMock()
        user_repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": True, "teams": []}

        with pytest.raises(InsufficientPermissions) as exc_info:
            await checker(request, {"oid": "u1", "name": "U"}, user_repo, AsyncMock())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_global_permission_does_not_need_device_path_param(self):
        checker = ABACPermissionCheck("platform.config.read")
        request = MagicMock()
        request.path_params = {}
        user_repo = AsyncMock()
        user_repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": True, "teams": []}

        result = await checker(request, {"oid": "u1", "name": "U"}, user_repo, AsyncMock())
        assert result["device_id"] is None

    def test_device_path_matches_route_path_params(self):
        """Every device-scoped ABACPermissionCheck must reference a parameter that exists in its route path."""
        from authorization.abac_permission_check import _GLOBAL_PERMISSIONS
        from main import app

        def walk(dependant):
            yield dependant
            for sub in dependant.dependencies:
                yield from walk(sub)

        mismatches = []
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            for dep in walk(dependant):
                call = dep.call
                if (
                    isinstance(call, ABACPermissionCheck)
                    and call.device_path
                    and call.permission not in _GLOBAL_PERMISSIONS
                    and call.device_path not in route.param_convertors
                ):
                    mismatches.append((route.path, call.permission, call.device_path))
        assert mismatches == []


# =============================================================================
# Tests for ABACDeviceCreateCheck
# =============================================================================


class TestABACDeviceCreateCheck:
    AUTH = {"oid": "user-oid-123", "name": "Test User"}

    def _request(self, body=None, invalid_json=False):
        request = MagicMock()
        request.path_params = {"device_id": "new-device"}
        if invalid_json:
            request.json = AsyncMock(side_effect=ValueError("bad json"))
        else:
            request.json = AsyncMock(return_value=body)
        return request

    def _team(self, actions, scope=None):
        return {"id": "t", "name": "T", "scope": scope, "roles": [{"id": "r", "name": "R", "allowed_actions": actions}]}

    def _repo(self, is_admin=False, teams=None):
        repo = AsyncMock()
        repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": is_admin, "teams": teams or []}
        return repo

    @pytest.mark.asyncio
    async def test_admin_bypasses_without_reading_body(self):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        request = self._request(body={"meta": {"region": "US"}})
        result = await ABACDeviceCreateCheck("device.create")(request, self.AUTH, self._repo(is_admin=True))
        assert result["device_id"] == "new-device"
        request.json.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_teams_raises_403(self):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        with pytest.raises(InsufficientPermissions) as exc:
            await ABACDeviceCreateCheck("device.create")(self._request({}), self.AUTH, self._repo())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_action_raises_403(self):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        repo = self._repo(teams=[self._team(["device.read"])])
        with pytest.raises(InsufficientPermissions) as exc:
            await ABACDeviceCreateCheck("device.create")(self._request({"meta": {}}), self.AUTH, repo)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unscoped_team_allows_any_meta(self):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        repo = self._repo(teams=[self._team(["device.create"])])
        result = await ABACDeviceCreateCheck("device.create")(
            self._request({"meta": {"region": "US"}}), self.AUTH, repo
        )
        assert result["permission"] == "device.create"

    @pytest.mark.asyncio
    async def test_scoped_team_matching_meta_allowed(self):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        scope = {"attr": {"region": "EU"}, "access_rule": "ALL"}
        repo = self._repo(teams=[self._team(["device.create"], scope)])
        await ABACDeviceCreateCheck("device.create")(self._request({"meta": {"region": "EU"}}), self.AUTH, repo)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [{"meta": {"region": "US"}}, {"meta": {}}, {}, {"meta": None}])
    async def test_scoped_team_nonmatching_or_missing_meta_denied(self, body):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        scope = {"attr": {"region": "EU"}, "access_rule": "ALL"}
        repo = self._repo(teams=[self._team(["device.create"], scope)])
        with pytest.raises(InsufficientPermissions) as exc:
            await ABACDeviceCreateCheck("device.create")(self._request(body), self.AUTH, repo)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_scope_of_team_without_action_is_ignored(self):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        eu = {"attr": {"region": "EU"}, "access_rule": "ALL"}
        us = {"attr": {"region": "US"}, "access_rule": "ALL"}
        repo = self._repo(teams=[self._team(["device.create"], eu), self._team(["device.read"], us)])
        with pytest.raises(InsufficientPermissions):
            await ABACDeviceCreateCheck("device.create")(self._request({"meta": {"region": "US"}}), self.AUTH, repo)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body,invalid", [([1], False), ({"meta": "x"}, False), (None, True)])
    async def test_malformed_body_returns_400(self, body, invalid):
        from authorization.abac_permission_check import ABACDeviceCreateCheck
        from exceptions import APIError
        scope = {"attr": {"region": "EU"}, "access_rule": "ALL"}
        repo = self._repo(teams=[self._team(["device.create"], scope)])
        with pytest.raises(APIError) as exc:
            await ABACDeviceCreateCheck("device.create")(self._request(body, invalid), self.AUTH, repo)
        assert exc.value.status_code == 400


# =============================================================================
# Tests for ABACResolvedDeviceCheck
# =============================================================================


class TestABACResolvedDeviceCheck:
    AUTH = {"oid": "user-oid-123", "name": "Test User"}
    PERM = "device.endpoint.read"
    EU = {"attr": {"region": "EU"}, "access_rule": "ALL"}

    def _request(self, path=None, query=None):
        request = MagicMock()
        request.path_params = path or {}
        request.query_params = query or {}
        return request

    def _team(self, actions, scope=None):
        return {"id": "t", "name": "T", "scope": scope, "roles": [{"id": "r", "name": "R", "allowed_actions": actions}]}

    def _repos(self, is_admin=False, teams=None, device_meta=None, endpoint_device=None, service_device=None):
        user_repo = AsyncMock()
        user_repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": is_admin, "teams": teams or []}
        device_repo = AsyncMock()
        device_repo.get_device_meta_raw.return_value = device_meta
        endpoint_repo = AsyncMock()
        endpoint_repo.get_endpoint_device_id.return_value = endpoint_device
        service_repo = AsyncMock()
        service_repo.get_service_device_id.return_value = service_device
        return user_repo, device_repo, endpoint_repo, service_repo

    async def _call(self, check, request, repos):
        return await check(request, self.AUTH, *repos)

    def test_invalid_owner_rejected(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        with pytest.raises(ValueError):
            ABACResolvedDeviceCheck(self.PERM, "bogus", "x")

    @pytest.mark.asyncio
    async def test_admin_bypasses_without_resolution(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(is_admin=True)
        result = await self._call(ABACResolvedDeviceCheck(self.PERM, "endpoint", "endpoint_id"), self._request(), repos)
        assert result["device_id"] is None
        repos[2].get_endpoint_device_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_teams_denied(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        with pytest.raises(InsufficientPermissions):
            await self._call(ABACResolvedDeviceCheck(self.PERM, "device", "device_id"), self._request(), self._repos())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("owner,param", [
        ("device", "device_id"), ("endpoint", "endpoint_id"), ("service", "service_id"),
    ])
    async def test_missing_action_denied_before_ownership_lookup(self, owner, param):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team(["device.read"])])
        with pytest.raises(InsufficientPermissions):
            await self._call(
                ABACResolvedDeviceCheck(self.PERM, owner, param), self._request(query={param: "d"}), repos
            )
        repos[1].get_device_meta_raw.assert_not_awaited()
        repos[2].get_endpoint_device_id.assert_not_awaited()
        repos[3].get_service_device_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unscoped_team_skips_resolution(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team([self.PERM])])
        result = await self._call(ABACResolvedDeviceCheck(self.PERM, "service", "service_id"), self._request(), repos)
        assert result["device_id"] is None
        repos[3].get_service_device_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_scoped_missing_param_denied(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team([self.PERM], self.EU)])
        with pytest.raises(InsufficientPermissions) as exc:
            await self._call(ABACResolvedDeviceCheck(self.PERM, "device", "device_id"), self._request(), repos)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("owner,param,kw", [
        ("device", "device_id", {}),
        ("endpoint", "endpoint_id", {"endpoint_device": "dev-1"}),
        ("service", "service_id", {"service_device": "dev-1"}),
    ])
    async def test_scoped_in_scope_allowed(self, owner, param, kw):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team([self.PERM], self.EU)], device_meta={"region": "EU"}, **kw)
        result = await self._call(
            ABACResolvedDeviceCheck(self.PERM, owner, param), self._request(query={param: "x"}), repos
        )
        assert result["device_id"] == ("dev-1" if kw else "x")
        repos[1].get_device_meta_raw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_path_param_is_read(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(
            teams=[self._team([self.PERM], self.EU)], device_meta={"region": "EU"}, endpoint_device="dev-1"
        )
        await self._call(
            ABACResolvedDeviceCheck(self.PERM, "endpoint", "endpoint_id"),
            self._request(path={"endpoint_id": "ep"}),
            repos,
        )
        repos[2].get_endpoint_device_id.assert_awaited_once_with("ep")

    @pytest.mark.asyncio
    async def test_scoped_out_of_scope_denied(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team([self.PERM], self.EU)], device_meta={"region": "US"})
        with pytest.raises(InsufficientPermissions) as exc:
            await self._call(
                ABACResolvedDeviceCheck(self.PERM, "device", "device_id"), self._request(query={"device_id": "d"}), repos
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("owner,param", [("endpoint", "endpoint_id"), ("service", "service_id")])
    @pytest.mark.parametrize("is_admin,scope", [(True, None), (False, None), (False, EU)])
    async def test_unknown_target_returns_404(self, owner, param, is_admin, scope):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(is_admin=is_admin, teams=[self._team([self.PERM], scope)])
        with pytest.raises(APIError) as exc:
            await self._call(
                ABACResolvedDeviceCheck(self.PERM, owner, param), self._request(query={param: "nope"}), repos
            )
        assert exc.value.status_code == 404
        repos[1].get_device_meta_raw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scoped_unknown_device_denied(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team([self.PERM], self.EU)], device_meta=None)
        with pytest.raises(InsufficientPermissions):
            await self._call(
                ABACResolvedDeviceCheck(self.PERM, "device", "device_id"), self._request(query={"device_id": "d"}), repos
            )

    @pytest.mark.asyncio
    async def test_device_missing_scope_attribute_fails_closed(self):
        from authorization.abac_permission_check import ABACResolvedDeviceCheck
        repos = self._repos(teams=[self._team([self.PERM], self.EU)], device_meta={})
        with pytest.raises(InsufficientPermissions):
            await self._call(
                ABACResolvedDeviceCheck(self.PERM, "device", "device_id"), self._request(query={"device_id": "d"}), repos
            )


# =============================================================================
# Tests for ABACAnyPermissionCheck
# =============================================================================


class TestABACAnyPermissionCheck:
    AUTH = {"oid": "u1", "name": "U"}

    def _repo(self, is_admin=False, actions=None):
        repo = AsyncMock()
        teams = [{"id": "t", "name": "T", "scope": None, "roles": [{"id": "r", "name": "R", "allowed_actions": actions}]}] if actions is not None else []
        repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": is_admin, "teams": teams}
        return repo

    def test_requires_at_least_one_permission(self):
        from authorization.abac_permission_check import ABACAnyPermissionCheck
        with pytest.raises(ValueError):
            ABACAnyPermissionCheck()

    @pytest.mark.asyncio
    async def test_admin_passes(self):
        from authorization.abac_permission_check import ABACAnyPermissionCheck
        await ABACAnyPermissionCheck("a.x", "b.y")(self.AUTH, self._repo(is_admin=True))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("actions", [["device.create"], ["platform.config.read"], ["device.create", "platform.config.read"]])
    async def test_any_one_permission_passes(self, actions):
        from authorization.abac_permission_check import ABACAnyPermissionCheck
        result = await ABACAnyPermissionCheck("platform.config.read", "device.create")(self.AUTH, self._repo(actions=actions))
        assert result["permission"] in actions

    @pytest.mark.asyncio
    async def test_none_granted_denied(self):
        from authorization.abac_permission_check import ABACAnyPermissionCheck
        with pytest.raises(InsufficientPermissions) as exc:
            await ABACAnyPermissionCheck("platform.config.read", "device.create")(self.AUTH, self._repo(actions=["device.read"]))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_teams_denied(self):
        from authorization.abac_permission_check import ABACAnyPermissionCheck
        with pytest.raises(InsufficientPermissions):
            await ABACAnyPermissionCheck("device.create")(self.AUTH, self._repo())
