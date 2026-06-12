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
            await self._devices.create_device(device_id=device_id, metadata=meta)
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
