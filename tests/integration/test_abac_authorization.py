"""
ABAC authorization integration tests.

Structure
---------
Each test class sets up its own fixture data (users, scopes, roles, teams,
devices) via the ``AbacFixtures`` helper and calls endpoints as a specific
user by overriding ``fake_jwt_user``.

AbacFixtures.setup() inserts all seed data in a single call and returns
an ``AbacWorld`` dataclass with all created IDs ready to use.

Because every test method runs inside a rolled-back transaction (see
conftest.py) the data from one test is never visible to the next.

Permission strings (seeded by the ``seed_actions`` Alembic migration)
----------------------------------------------------------------------
Platform (global, no device scope):
    platform.authorization.read
    platform.authorization.write

Device (evaluated against device_meta when a scope is set):
    device.read
    device.deployment.write  /  device.module.execute_method
    device.network.write  /  device.network.discover
    device.line.write
    device.password.read  /  device.password.write
    device.module_twin_config.write
    device.sems_template.apply
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from db.registry import repo_registry
from db.repos.device import DeviceRepository
from db.repos.role import RoleRepository
from db.repos.scope import ScopeRepository
from db.repos.team import TeamRepository
from db.repos.user import UserRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _repo(interface, session: AsyncSession):
    cls = repo_registry.get(interface)
    return cls(session)


# The 'default' device type requires these fields; tests only care about
# scope-matching keys (e.g. "region"), so fill in placeholders for the rest.
_DEFAULT_TYPE_REQUIRED_FIELDS = {
    "description": "test device",
    "countryCode": "XX",
    "city": "Test City",
    "geoLocation": "0,0",
}


@dataclass
class AbacWorld:
    """
    Holds all IDs produced by ``AbacFixtures.setup()``.

    ``users``   : logical_name -> OID string
    ``roles``   : logical_name -> role UUID
    ``teams``   : logical_name -> team UUID
    ``scopes``  : logical_name -> scope UUID
    ``devices`` : device_id   -> meta dict
    """
    users: dict[str, str] = field(default_factory=dict)
    roles: dict[str, UUID] = field(default_factory=dict)
    teams: dict[str, UUID] = field(default_factory=dict)
    scopes: dict[str, UUID] = field(default_factory=dict)
    devices: dict[str, dict] = field(default_factory=dict)


class AbacFixtures:
    """
    Ergonomic helper for seeding ABAC test data through the real repositories.

    All writes use the per-test ``AsyncSession``, so they are automatically
    rolled back when the test ends — no cleanup required.

    Usage::

        world = await AbacFixtures(db_session).setup(
            users={"viewer": "viewer-oid"},
            scopes={"europe": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"reader": ["device.read"]},
            teams={
                "eu-team": {
                    "scope": "europe",       # logical name from scopes dict
                    "roles": ["reader"],     # logical names from roles dict
                    "users": ["viewer"],     # logical names from users dict
                },
            },
            devices={"berlin-01": {"region": "EU", "site": "Berlin"}},
        )
        # world.users["viewer"] == "viewer-oid"
        # world.roles["reader"] == UUID(...)
        # world.teams["eu-team"] == UUID(...)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._users: UserRepository = _repo(UserRepository, session)
        self._teams: TeamRepository = _repo(TeamRepository, session)
        self._roles: RoleRepository = _repo(RoleRepository, session)
        self._scopes: ScopeRepository = _repo(ScopeRepository, session)
        self._devices: DeviceRepository = _repo(DeviceRepository, session)

    async def setup(
        self,
        users: dict[str, str] | None = None,
        scopes: dict[str, dict] | None = None,
        roles: dict[str, list[str]] | None = None,
        teams: dict[str, dict] | None = None,
        devices: dict[str, dict] | None = None,
    ) -> AbacWorld:
        """
        Create all seed data and return an ``AbacWorld`` with the created IDs.

        Parameters
        ----------
        users:
            ``{logical_name: oid}`` — created as non-admin users.
        scopes:
            ``{logical_name: {"attr": {...}, "access_rule": "ALL"|"ANY",
                              "description": "..."}}``
        roles:
            ``{logical_name: [action_name, ...]}``
            Action names must already exist in the seeded ``actions`` table.
        teams:
            ``{logical_name: {"scope": scope_logical_name | None,
                              "roles": [role_logical_name, ...],
                              "users": [user_logical_name, ...]}}``
        devices:
            ``{device_id: {meta_key: meta_value, ...}}``
        """
        w = AbacWorld()

        for name, oid in (users or {}).items():
            await self._users.create(
                user_id=oid,
                preferred_username=f"{name}@test.com",
                is_admin=False,
            )
            w.users[name] = oid

        for name, cfg in (scopes or {}).items():
            r = await self._scopes.create(
                name=name,
                attr=cfg["attr"],
                access_rule=cfg.get("access_rule", "ALL"),
                description=cfg.get("description"),
            )
            w.scopes[name] = r["id"]

        for name, action_names in (roles or {}).items():
            r = await self._roles.create_role(
                name=name,
                description=None,
                action_names=action_names,
            )
            w.roles[name] = r["id"]

        for name, cfg in (teams or {}).items():
            scope_id = w.scopes.get(cfg["scope"]) if cfg.get("scope") else None
            r = await self._teams.create(
                name=name,
                scope_id=scope_id,
                user_ids=[w.users[u] for u in cfg.get("users", [])],
                role_ids=[w.roles[ro] for ro in cfg.get("roles", [])],
            )
            w.teams[name] = r["id"]

        for device_id, meta in (devices or {}).items():
            full_meta = {**_DEFAULT_TYPE_REQUIRED_FIELDS, **meta}
            await self._devices.create_device(device_id=device_id, metadata=full_meta)
            w.devices[device_id] = meta

        return w


# ===========================================================================
# Tests: platform authorization endpoints (no device scope)
# ===========================================================================

class TestPlatformAuthorizationEndpoints:
    """
    Tests for /auth/scopes — requires platform.authorization.read|write.

    Device scope is never evaluated for these endpoints; the role->action
    chain alone gates access.
    """

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "platform-test-oid",
            "sub": "platform-test-oid",
            "preferred_username": "platform-test@test.com",
            "name": "Platform Test",
            "roles": [],
        }

    async def test_user_without_authorization_read_cannot_list_scopes(
        self, client, db_session
    ):
        """A user whose role only grants device.read cannot list scopes."""
        await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
            roles={"device-reader": ["device.read"]},
            teams={"t": {"roles": ["device-reader"], "users": ["tester"]}},
        )

        response = await client.get("/auth/scopes")
        assert response.status_code == 403

    async def test_user_with_authorization_read_can_list_scopes(
        self, client, db_session
    ):
        """A user with platform.authorization.read in their role gets 200."""
        await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
            scopes={"scope-a": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"auth-reader": ["platform.authorization.read"]},
            teams={"t": {"scope": "scope-a", "roles": ["auth-reader"], "users": ["tester"]}},
        )

        response = await client.get("/auth/scopes")
        assert response.status_code == 200
        scopes = response.json()
        assert isinstance(scopes, list)

        scope_a = next(scope for scope in scopes if scope["name"] == "scope-a")
        assert scope_a["team_usage_count"] == 1

    async def test_read_role_cannot_create_scope(self, client, db_session):
        """platform.authorization.read is not enough for POST /auth/scopes."""
        await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
            roles={"auth-reader": ["platform.authorization.read"]},
            teams={"t": {"roles": ["auth-reader"], "users": ["tester"]}},
        )

        payload = {"name": "should-fail", "attr": {}, "access_rule": "ALL"}
        response = await client.post("/auth/scopes", json=payload)
        assert response.status_code == 403

    async def test_write_role_can_create_scope(self, client, db_session):
        """platform.authorization.write is sufficient for POST /auth/scopes."""
        await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
            roles={"auth-writer": ["platform.authorization.write"]},
            teams={"t": {"roles": ["auth-writer"], "users": ["tester"]}},
        )

        payload = {"name": "created-scope", "attr": {}, "access_rule": "ALL"}
        response = await client.post("/auth/scopes", json=payload)
        assert response.status_code == 200
        assert response.json()["name"] == "created-scope"

    async def test_user_with_no_team_gets_403(self, client, db_session):
        """A user who exists but belongs to no team is always rejected."""
        await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
        )

        response = await client.get("/auth/scopes")
        assert response.status_code == 403

    async def test_user_with_authorization_read_can_get_role_details(
        self, client, db_session
    ):
        """A user with platform.authorization.read can fetch role details by id."""
        world = await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
            roles={
                "auth-reader": ["platform.authorization.read"],
                "device-reader": ["device.read"],
            },
            teams={
                "role-consumers": {
                    "roles": ["device-reader", "auth-reader"],
                    "users": ["tester"],
                }
            },
        )

        target_role_id = world.roles["device-reader"]
        response = await client.get(f"/auth/roles/{target_role_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == str(target_role_id)
        assert payload["name"] == "device-reader"
        assert payload["actions"] == ["device.read"]
        assert payload["teams"] == [
            {
                "id": str(world.teams["role-consumers"]),
                "name": "role-consumers",
            }
        ]

    async def test_user_with_authorization_read_can_list_teams_with_roles_scope_and_user_count(
        self, client, db_session
    ):
        """GET /auth/teams returns role list, scope object, and assigned user count."""
        world = await AbacFixtures(db_session).setup(
            users={
                "tester": "platform-test-oid",
                "member": "platform-member-oid",
            },
            scopes={
                "ops-scope": {
                    "attr": {"region": "EU"},
                    "access_rule": "ALL",
                    "description": "Operations",
                }
            },
            roles={
                "auth-reader": ["platform.authorization.read"],
                "device-reader": ["device.read"],
            },
            teams={
                "ops-team": {
                    "scope": "ops-scope",
                    "roles": ["auth-reader", "device-reader"],
                    "users": ["tester", "member"],
                }
            },
        )

        response = await client.get("/auth/teams")
        assert response.status_code == 200

        teams = response.json()
        ops_team = next(team for team in teams if team["name"] == "ops-team")
        assert ops_team["id"] == str(world.teams["ops-team"])
        assert ops_team["user_count"] == 2
        assert ops_team["scope"]["id"] == str(world.scopes["ops-scope"])
        assert ops_team["scope"]["name"] == "ops-scope"
        assert ops_team["scope"]["attr"] == {"region": "EU"}
        role_names = {role["name"] for role in ops_team["roles"]}
        assert role_names == {"auth-reader", "device-reader"}

    async def test_user_with_authorization_write_can_replace_role_actions(
        self, client, db_session
    ):
        """PUT /auth/roles/{id} replaces assigned actions with requested list."""
        world = await AbacFixtures(db_session).setup(
            users={"tester": "platform-test-oid"},
            roles={
                "auth-writer": ["platform.authorization.write"],
                "managed-role": ["device.read", "device.network.discover"],
            },
            teams={
                "writers": {
                    "roles": ["auth-writer"],
                    "users": ["tester"],
                }
            },
        )

        target_role_id = world.roles["managed-role"]
        response = await client.put(
            f"/auth/roles/{target_role_id}",
            json={
                "name": "managed-role",
                "description": "updated role",
                "actions": ["device.network.discover", "device.line.write"],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == str(target_role_id)
        assert payload["description"] == "updated role"
        assert payload["actions"] == ["device.line.write", "device.network.discover"]

    async def test_assigning_user_to_team_marks_user_as_not_new(
        self, client, db_session
    ):
        """When assigning a user to a team, their is_new flag is set to false."""
        await AbacFixtures(db_session).setup(
            users={
                "tester": "platform-test-oid",
                "new-user": "brand-new-oid",
            },
            roles={
                "auth-admin": [
                    "platform.authorization.read",
                    "platform.authorization.write",
                ]
            },
            teams={
                "admins": {
                    "roles": ["auth-admin"],
                    "users": ["tester"],
                }
            },
        )

        before_response = await client.get("/auth/users?is_new=true")
        assert before_response.status_code == 200
        before_ids = {user["id"] for user in before_response.json()}
        assert "brand-new-oid" in before_ids

        create_team_response = await client.post(
            "/auth/teams",
            json={"name": "target-team", "role_ids": []},
        )
        assert create_team_response.status_code == 200
        created_team_id = create_team_response.json()["id"]

        add_user_response = await client.post(
            f"/auth/teams/{created_team_id}/users",
            json={"user_id": "brand-new-oid"},
        )
        assert add_user_response.status_code == 200

        after_new_response = await client.get("/auth/users?is_new=true")
        assert after_new_response.status_code == 200
        after_new_ids = {user["id"] for user in after_new_response.json()}
        assert "brand-new-oid" not in after_new_ids

        after_existing_response = await client.get("/auth/users?is_new=false")
        assert after_existing_response.status_code == 200
        after_existing_ids = {user["id"] for user in after_existing_response.json()}
        assert "brand-new-oid" in after_existing_ids


# ===========================================================================
# Tests: device list endpoint with scope-based filtering
# ===========================================================================

class TestDeviceListScopeFiltering:
    """
    Tests for GET /devices — uses ABACDeviceListFilter.

    The filter is applied in-process against device_meta rows in the DB,
    so real device rows must be created before each test.
    """

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "device-test-oid",
            "sub": "device-test-oid",
            "preferred_username": "device-test@test.com",
            "name": "Device Test",
            "roles": [],
        }

    async def test_unrestricted_team_sees_all_devices(self, client, db_session):
        """Team with no scope -> is_unrestricted=True -> all devices returned."""
        await AbacFixtures(db_session).setup(
            users={"tester": "device-test-oid"},
            roles={"reader": ["device.read"]},
            teams={"all-access": {"roles": ["reader"], "users": ["tester"]}},
            devices={
                "berlin-01": {"region": "EU"},
                "tokyo-01":  {"region": "AP"},
            },
        )

        response = await client.get("/devices")
        assert response.status_code == 200
        ids = {d["deviceId"] for d in response.json()}
        assert {"berlin-01", "tokyo-01"} <= ids

    async def test_scoped_user_sees_only_matching_devices(
        self, client, db_session
    ):
        """Scope attr region=EU: EU device visible, US device hidden."""
        await AbacFixtures(db_session).setup(
            users={"tester": "device-test-oid"},
            scopes={"eu-scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"reader": ["device.read"]},
            teams={
                "eu-team": {
                    "scope": "eu-scope",
                    "roles": ["reader"],
                    "users": ["tester"],
                }
            },
            devices={
                "berlin-01":   {"region": "EU"},
                "new-york-01": {"region": "US"},
            },
        )

        response = await client.get("/devices")
        assert response.status_code == 200
        ids = {d["deviceId"] for d in response.json()}
        assert "berlin-01" in ids
        assert "new-york-01" not in ids

    async def test_list_value_scope_uses_containment_check(
        self, client, db_session
    ):
        """
        Scope attr region=[EU, AP]: list value triggers containment.
        Devices with EU or AP match; US does not.
        """
        await AbacFixtures(db_session).setup(
            users={"tester": "device-test-oid"},
            scopes={
                "multi-region": {
                    "attr": {"region": ["EU", "AP"]},
                    "access_rule": "ALL",
                }
            },
            roles={"reader": ["device.read"]},
            teams={
                "multi-team": {
                    "scope": "multi-region",
                    "roles": ["reader"],
                    "users": ["tester"],
                }
            },
            devices={
                "berlin-01":   {"region": "EU"},
                "tokyo-01":    {"region": "AP"},
                "new-york-01": {"region": "US"},
            },
        )

        response = await client.get("/devices")
        assert response.status_code == 200
        ids = {d["deviceId"] for d in response.json()}
        assert "berlin-01" in ids
        assert "tokyo-01" in ids
        assert "new-york-01" not in ids

    async def test_user_without_device_read_gets_403(self, client, db_session):
        """Role with only platform.authorization.read cannot call GET /devices."""
        await AbacFixtures(db_session).setup(
            users={"tester": "device-test-oid"},
            roles={"auth-reader": ["platform.authorization.read"]},
            teams={"t": {"roles": ["auth-reader"], "users": ["tester"]}},
        )

        response = await client.get("/devices")
        assert response.status_code == 403

    async def test_one_unrestricted_team_overrides_scoped_team(
        self, client, db_session
    ):
        """
        User in both a scoped (EU) team and an unrestricted team.
        The unrestricted team wins -> all devices visible.
        """
        await AbacFixtures(db_session).setup(
            users={"tester": "device-test-oid"},
            scopes={"eu-scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"reader": ["device.read"]},
            teams={
                "eu-team": {
                    "scope": "eu-scope",
                    "roles": ["reader"],
                    "users": ["tester"],
                },
                "global-team": {
                    "roles": ["reader"],
                    "users": ["tester"],
                },
            },
            devices={
                "berlin-01":   {"region": "EU"},
                "new-york-01": {"region": "US"},
            },
        )

        response = await client.get("/devices")
        assert response.status_code == 200
        ids = {d["deviceId"] for d in response.json()}
        assert "berlin-01" in ids
        assert "new-york-01" in ids


_DEVICE_DETAIL = {
    "deviceId": "ok",
    "connectionStatus": {
        "deviceStatus": "Unknown",
        "iotEdgeRuntime": "Unknown",
        "iotHub": "Unknown",
        "sems": "Unknown",
        "vpn": "Unknown",
    },
    "deviceMetadata": {},
}


_DEVICE_DETAIL = {
    "deviceId": "ok",
    "connectionStatus": {
        "deviceStatus": "Unknown",
        "iotEdgeRuntime": "Unknown",
        "iotHub": "Unknown",
        "sems": "Unknown",
        "vpn": "Unknown",
    },
    "deviceMetadata": {},
}


# ===========================================================================
# Tests: device lifecycle routes (GET/PUT/DELETE /devices/{device_id})
# ===========================================================================

class TestDeviceLifecycleAuthorization:
    """
    Authorization for GET, PUT and DELETE on a single device. The downstream
    handlers (IoT Hub / SEMS) are mocked; only the ABAC outcome is under test.
    """

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "lifecycle-oid",
            "sub": "lifecycle-oid",
            "preferred_username": "lifecycle@test.com",
            "name": "Lifecycle Tester",
            "roles": [],
        }

    @pytest.fixture(autouse=True)
    def mock_handlers(self, monkeypatch):
        from unittest.mock import AsyncMock
        import routers.devices.router as device_router

        monkeypatch.setattr(device_router, "get_device", AsyncMock(return_value=_DEVICE_DETAIL))
        monkeypatch.setattr(device_router, "create_device", AsyncMock(return_value={"device_id": "ok"}))
        monkeypatch.setattr(device_router, "delete_device", AsyncMock(return_value=None))

    async def _world(self, db_session, actions, scoped=True):
        return await AbacFixtures(db_session).setup(
            users={"tester": "lifecycle-oid"},
            scopes={"eu-scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"r": actions},
            teams={"t": {"scope": "eu-scope" if scoped else None, "roles": ["r"], "users": ["tester"]}},
            devices={"berlin-01": {"region": "EU"}, "new-york-01": {"region": "US"}},
        )

    # --- GET ---

    async def test_get_in_scope_device_allowed(self, client, db_session):
        await self._world(db_session, ["device.read"])
        assert (await client.get("/devices/berlin-01")).status_code == 200

    async def test_get_out_of_scope_device_denied(self, client, db_session):
        await self._world(db_session, ["device.read"])
        assert (await client.get("/devices/new-york-01")).status_code == 403

    async def test_get_without_action_denied(self, client, db_session):
        await self._world(db_session, ["device.delete"])
        assert (await client.get("/devices/berlin-01")).status_code == 403

    # --- DELETE ---

    async def test_delete_in_scope_device_allowed(self, client, db_session):
        await self._world(db_session, ["device.delete"])
        assert (await client.delete("/devices/berlin-01")).status_code == 200

    async def test_delete_out_of_scope_device_denied(self, client, db_session):
        await self._world(db_session, ["device.delete"])
        assert (await client.delete("/devices/new-york-01")).status_code == 403

    async def test_delete_with_only_read_or_metadata_write_denied(self, client, db_session):
        await self._world(db_session, ["device.read", "device.metadata.write"])
        assert (await client.delete("/devices/berlin-01")).status_code == 403

    # --- PUT (create) ---

    async def test_create_without_action_denied(self, client, db_session):
        await self._world(db_session, ["device.read", "device.delete"])
        response = await client.put("/devices/brand-new", json={"meta": {"region": "EU"}})
        assert response.status_code == 403

    async def test_create_matching_meta_allowed(self, client, db_session):
        await self._world(db_session, ["device.create"])
        response = await client.put("/devices/brand-new", json={"meta": {"region": "EU"}})
        assert response.status_code == 200

    async def test_create_nonmatching_meta_denied(self, client, db_session):
        await self._world(db_session, ["device.create"])
        response = await client.put("/devices/brand-new", json={"meta": {"region": "US"}})
        assert response.status_code == 403

    async def test_create_missing_meta_denied_for_scoped_user(self, client, db_session):
        await self._world(db_session, ["device.create"])
        assert (await client.put("/devices/brand-new", json={})).status_code == 403

    async def test_create_any_meta_allowed_for_unscoped_team(self, client, db_session):
        await self._world(db_session, ["device.create"], scoped=False)
        response = await client.put("/devices/brand-new", json={"meta": {"region": "US"}})
        assert response.status_code == 200


class TestDeviceLifecycleAdminBypass:
    """Default fake user is an admin: bypass applies to all lifecycle routes."""

    @pytest.fixture(autouse=True)
    def mock_handlers(self, monkeypatch):
        from unittest.mock import AsyncMock
        import routers.devices.router as device_router

        monkeypatch.setattr(device_router, "get_device", AsyncMock(return_value=_DEVICE_DETAIL))
        monkeypatch.setattr(device_router, "create_device", AsyncMock(return_value={"device_id": "ok"}))
        monkeypatch.setattr(device_router, "delete_device", AsyncMock(return_value=None))

    async def test_admin_can_use_lifecycle_routes(self, client):
        assert (await client.get("/devices/any")).status_code == 200
        assert (await client.put("/devices/any", json={"meta": {"region": "XX"}})).status_code == 200
        assert (await client.delete("/devices/any")).status_code == 200


# ===========================================================================
# Tests: endpoint and service instance routes (owning-device scope)
# ===========================================================================

class TestEndpointServiceScope:
    """
    /endpoints and /services identify a device directly (?device_id=) or
    indirectly (endpoint_id / service_id). Scope is evaluated on the owning device.
    Type routes are global and are not scope-checked.
    """

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "ep-oid",
            "sub": "ep-oid",
            "preferred_username": "ep@test.com",
            "name": "Endpoint Tester",
            "roles": [],
        }

    async def _world(self, db_session, actions=("device.endpoint.read", "device.endpoint.write"), scoped=True):
        from db.models.endpoint import Endpoint, EndpointType
        from db.models.service import Service, ServiceType

        await AbacFixtures(db_session).setup(
            users={"tester": "ep-oid"},
            scopes={"eu-scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"r": list(actions)},
            teams={"t": {"scope": "eu-scope" if scoped else None, "roles": ["r"], "users": ["tester"]}},
            devices={"berlin-01": {"region": "EU"}, "new-york-01": {"region": "US"}},
        )
        db_session.add_all([
            EndpointType(type_id="et", label="et", description=None, fields={}, mapping={}),
            ServiceType(type_id="st", label="st", description=None, fields={}, mapping={}),
        ])
        await db_session.flush()
        db_session.add_all([
            Endpoint(endpoint_id="ep-eu", device_id="berlin-01", type_id="et", endpoint_data={}),
            Endpoint(endpoint_id="ep-us", device_id="new-york-01", type_id="et", endpoint_data={}),
        ])
        await db_session.flush()
        db_session.add_all([
            Service(service_id="svc-eu", endpoint_id="ep-eu", type_id="st", service_data={}),
            Service(service_id="svc-us", endpoint_id="ep-us", type_id="st", service_data={}),
        ])
        await db_session.flush()

    # --- endpoints ---

    async def test_list_endpoints_in_scope_allowed(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/endpoints", params={"device_id": "berlin-01"})).status_code == 200

    async def test_list_endpoints_out_of_scope_denied(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/endpoints", params={"device_id": "new-york-01"})).status_code == 403

    async def test_create_endpoint_out_of_scope_denied(self, client, db_session):
        await self._world(db_session)
        response = await client.post(
            "/endpoints", params={"device_id": "new-york-01"}, json={"type_id": "et", "endpoint_data": {}}
        )
        assert response.status_code == 403

    async def test_endpoint_by_id_in_scope_allowed(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/endpoints/ep-eu")).status_code == 200

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    async def test_endpoint_by_id_out_of_scope_denied(self, client, db_session, method):
        await self._world(db_session)
        kwargs = {"json": {"endpoint_data": {}}} if method == "patch" else {}
        assert (await getattr(client, method)("/endpoints/ep-us", **kwargs)).status_code == 403

    async def test_endpoint_unknown_id_returns_404_for_authorized_scoped_user(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/endpoints/does-not-exist")).status_code == 404

    @pytest.mark.parametrize("path,patch", [
        ("/endpoints/does-not-exist", {"endpoint_data": {}}),
        ("/services/does-not-exist", {"service_data": {}}),
    ])
    @pytest.mark.parametrize("method,action", [
        ("GET", "device.read"),
        ("PATCH", "device.endpoint.read"),
        ("DELETE", "device.endpoint.read"),
    ])
    async def test_missing_action_denied_before_missing_resource_lookup(
        self, client, db_session, path, patch, method, action
    ):
        await self._world(db_session, actions=(action,))
        response = await client.request(method, path, json=patch if method == "PATCH" else None)
        assert response.status_code == 403

    async def test_endpoint_write_requires_write_action(self, client, db_session):
        await self._world(db_session, actions=("device.endpoint.read",))
        assert (await client.delete("/endpoints/ep-eu")).status_code == 403

    async def test_endpoint_unscoped_team_reaches_any_device(self, client, db_session):
        await self._world(db_session, scoped=False)
        assert (await client.get("/endpoints/ep-us")).status_code == 200

    # --- services ---

    async def test_list_services_in_scope_allowed(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/services", params={"endpoint_id": "ep-eu"})).status_code == 200

    async def test_list_services_out_of_scope_denied(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/services", params={"endpoint_id": "ep-us"})).status_code == 403

    async def test_create_service_out_of_scope_denied(self, client, db_session):
        await self._world(db_session)
        response = await client.post(
            "/services", params={"endpoint_id": "ep-us"}, json={"type_id": "st", "service_data": {}}
        )
        assert response.status_code == 403

    async def test_service_by_id_in_scope_allowed(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/services/svc-eu")).status_code == 200

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    async def test_service_by_id_out_of_scope_denied(self, client, db_session, method):
        await self._world(db_session)
        kwargs = {"json": {"service_data": {}}} if method == "patch" else {}
        assert (await getattr(client, method)("/services/svc-us", **kwargs)).status_code == 403

    async def test_service_unknown_id_returns_404_for_authorized_scoped_user(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/services/does-not-exist")).status_code == 404

    # --- global types are not device scoped ---

    async def test_type_routes_not_scope_checked(self, client, db_session):
        await self._world(db_session)
        assert (await client.get("/endpoint-types")).status_code == 200
        assert (await client.get("/service-types")).status_code == 200


class TestEndpointServiceAdmin:
    async def test_admin_unknown_ids_return_404(self, client):
        assert (await client.get("/endpoints/does-not-exist")).status_code == 404
        assert (await client.get("/services/does-not-exist")).status_code == 404


class TestDeviceMetaValuesAuthorization:
    """GET /devices/meta-values needs device.read and only reports values from in-scope devices."""

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "mv-oid",
            "sub": "mv-oid",
            "preferred_username": "mv@test.com",
            "name": "MetaValues Tester",
            "roles": [],
        }

    async def _world(self, db_session, actions, scoped=True):
        await AbacFixtures(db_session).setup(
            users={"tester": "mv-oid"},
            scopes={"eu-scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"r": actions},
            teams={"t": {"scope": "eu-scope" if scoped else None, "roles": ["r"], "users": ["tester"]}},
            devices={"berlin-01": {"region": "EU"}, "new-york-01": {"region": "US"}},
        )

    async def test_scoped_user_sees_only_in_scope_values(self, client, db_session):
        await self._world(db_session, ["device.read"])
        response = await client.get("/devices/meta-values")
        assert response.status_code == 200
        assert response.json()["region"] == ["EU"]

    async def test_unscoped_team_sees_all_values(self, client, db_session):
        await self._world(db_session, ["device.read"], scoped=False)
        assert (await client.get("/devices/meta-values")).json()["region"] == ["EU", "US"]

    async def test_without_device_read_denied(self, client, db_session):
        await self._world(db_session, ["device.endpoint.read"])
        assert (await client.get("/devices/meta-values")).status_code == 403


class TestGlobalConfigRoutes:
    """Compose deployments, active deployment, /deployments and the default template need platform.config.*."""

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "cfg-oid",
            "sub": "cfg-oid",
            "preferred_username": "cfg@test.com",
            "name": "Config Tester",
            "roles": [],
        }

    _READS = [
        ("get", "/compose-deployments"),
        ("get", "/compose-deployments/x"),
        ("get", "/active-deployment"),
        ("get", "/deployments"),
        ("get", "/smartems/default-template?device_type=t"),
    ]
    _WRITES = [
        ("put", "/compose-deployments/x"),
        ("delete", "/compose-deployments/x"),
        ("put", "/active-deployment/x"),
        ("delete", "/active-deployment"),
    ]

    async def _world(self, db_session, actions):
        await AbacFixtures(db_session).setup(
            users={"tester": "cfg-oid"},
            scopes={},
            roles={"r": actions},
            teams={"t": {"scope": None, "roles": ["r"], "users": ["tester"]}},
            devices={},
        )

    @pytest.mark.parametrize("method,url", _READS + _WRITES)
    async def test_without_any_config_action_denied(self, client, db_session, method, url):
        await self._world(db_session, ["device.read"])
        assert (await getattr(client, method)(url)).status_code == 403

    @pytest.mark.parametrize("method,url", _WRITES)
    async def test_read_action_cannot_write(self, client, db_session, method, url):
        await self._world(db_session, ["platform.config.read"])
        assert (await getattr(client, method)(url)).status_code == 403

    @pytest.mark.parametrize("method,url", [("get", "/compose-deployments"), ("get", "/active-deployment")])
    async def test_read_action_allowed(self, client, db_session, method, url):
        await self._world(db_session, ["platform.config.read"])
        assert (await getattr(client, method)(url)).status_code == 200


class TestTypeRoutesAuthorization:
    """
    Global type definitions: reads are open to the roles that need them,
    changes require platform.config.write.
    """

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "ty-oid",
            "sub": "ty-oid",
            "preferred_username": "ty@test.com",
            "name": "Types Tester",
            "roles": [],
        }

    _TYPE_WRITES = [
        ("post", "/endpoint-types"),
        ("patch", "/endpoint-types/x"),
        ("delete", "/endpoint-types/x"),
        ("post", "/service-types"),
        ("patch", "/service-types/x"),
        ("delete", "/service-types/x"),
        ("post", "/device-types"),
        ("patch", "/device-types/x"),
        ("delete", "/device-types/x"),
        ("post", "/network/default-scan-ports"),
        ("delete", "/network/default-scan-ports/80"),
    ]

    async def _world(self, db_session, actions):
        await AbacFixtures(db_session).setup(
            users={"tester": "ty-oid"},
            scopes={},
            roles={"r": actions},
            teams={"t": {"scope": None, "roles": ["r"], "users": ["tester"]}},
            devices={},
        )

    @pytest.mark.parametrize("method,url", _TYPE_WRITES)
    async def test_endpoint_write_action_cannot_change_global_types(self, client, db_session, method, url):
        await self._world(db_session, ["device.endpoint.read", "device.endpoint.write", "device.create"])
        assert (await client.request(method.upper(), url, json={})).status_code == 403

    @pytest.mark.parametrize("url", ["/endpoint-types", "/service-types", "/network/default-scan-ports"])
    async def test_endpoint_read_action_can_read_types(self, client, db_session, url):
        await self._world(db_session, ["device.endpoint.read"])
        assert (await client.get(url)).status_code == 200

    @pytest.mark.parametrize("actions", [["device.create"], ["device.read"], ["platform.config.read"]])
    async def test_device_types_readable_by_device_reader_creator_or_platform_reader(self, client, db_session, actions):
        await self._world(db_session, actions)
        assert (await client.get("/device-types")).status_code == 200

    async def test_device_types_hidden_without_any_read_action(self, client, db_session):
        await self._world(db_session, ["device.endpoint.read"])
        assert (await client.get("/device-types")).status_code == 403

    async def test_platform_config_write_can_change_types(self, client, db_session):
        await self._world(db_session, ["platform.config.write"])
        assert (await client.delete("/endpoint-types/does-not-exist")).status_code != 403
        assert (await client.delete("/device-types/does-not-exist")).status_code != 403


class TestMetadataPatchScope:
    """A metadata patch must leave the device inside the caller's scope."""

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "mp-oid",
            "sub": "mp-oid",
            "preferred_username": "mp@test.com",
            "name": "Metadata Tester",
            "roles": [],
        }

    async def _world(self, db_session, scoped=True):
        await AbacFixtures(db_session).setup(
            users={"tester": "mp-oid"},
            scopes={"eu-scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
            roles={"r": ["device.metadata.write"]},
            teams={"t": {"scope": "eu-scope" if scoped else None, "roles": ["r"], "users": ["tester"]}},
            devices={"berlin-01": {"region": "EU"}, "new-york-01": {"region": "US"}},
        )

    async def test_patch_non_scope_attribute_allowed(self, client, db_session):
        await self._world(db_session)
        response = await client.patch("/berlin-01/metadata", json={"city": "Berlin"})
        assert response.status_code == 200

    async def test_patch_that_leaves_scope_denied_and_not_applied(self, client, db_session):
        await self._world(db_session)
        assert (await client.patch("/berlin-01/metadata", json={"region": "US"})).status_code == 403
        assert (await client.patch("/berlin-01/metadata", json={"city": "x"})).status_code == 200

    async def test_patch_keeping_scope_value_allowed(self, client, db_session):
        await self._world(db_session)
        assert (await client.patch("/berlin-01/metadata", json={"region": "EU"})).status_code == 200

    async def test_patch_out_of_scope_device_denied(self, client, db_session):
        await self._world(db_session)
        assert (await client.patch("/new-york-01/metadata", json={"city": "x"})).status_code == 403

    async def test_patch_removing_scope_attribute_denied(self, client, db_session):
        await self._world(db_session)
        assert (await client.patch("/berlin-01/metadata", json={"region": None})).status_code == 403

    async def test_non_object_body_rejected(self, client, db_session):
        await self._world(db_session)
        assert (await client.patch("/berlin-01/metadata", json=["x"])).status_code == 400

    async def test_unscoped_team_may_change_any_attribute(self, client, db_session):
        await self._world(db_session, scoped=False)
        assert (await client.patch("/berlin-01/metadata", json={"region": "US"})).status_code == 200
