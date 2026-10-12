"""
Integration tests for the /extensions management routes: DB-backed
persistence, RBAC action enrollment, the enable/disable route-mounting lifecycle,
and internal-key rotation.

The field-ingress side app and its device-key auth channel are deliberately not
implemented (skipped for now, not just deferred to a later stage) so there is no
device-key coverage here.

Route mounting is verified by inspecting the live route tables of the two apps
(`main.app`, `extensions.setup.internal_app`) rather than by calling the mounted
routes themselves — actual dispatch through a mounted route is covered separately
in test_extensions_dispatch.py (see extensions/upstreams/http.py, extensions/upstreams/iotedge.py).
"""
import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.models.action import Action
from db.models.extension import Extension as ExtensionModel, ExtensionAction
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from extensions import registry
from extensions import setup as extensions_setup
from extensions.setup import _activate_enabled_module_schemas, _activate_module_schemas, internal_app
from extensions.upstreams import http as http_upstream
from routers.module_config.registry import module_schema_registry
from tests.integration.test_abac_authorization import AbacFixtures


def _unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _sample_registration(name: str, visibility: str = "public") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "Demo extension for tests",
        "upstreams": {
            "svc": {"type": "http", "base_url": "http://localhost:9000"},
        },
        "routes": [
            {
                "upstream": "svc",
                "path": f"/{name}/ping",
                "method": "GET",
                "upstream_path": "/ping",
                "visibility": visibility,
            },
        ],
    }


def _live_route_names(app, name: str) -> set:
    return {r.name for r in app.router.routes if getattr(r, "extension_name", None) == name}


async def test_disabling_extension_leaves_similarly_named_extension_mounted(client):
    from main import app as public_app

    name = _unique_name("prefix_ext")
    other = f"{name}__bar"
    for ext_name in (name, other):
        assert (await client.post("/extensions", json=_sample_registration(ext_name))).status_code == 201
        assert (await client.post(f"/extensions/{ext_name}/enable")).status_code == 200

    assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
    assert not _live_route_names(public_app, name)
    assert len(_live_route_names(public_app, other)) == 1

    for ext_name in (name, other):
        await client.delete(f"/extensions/{ext_name}")


async def test_database_rejects_multiple_extension_owners_for_one_action(db_session):
    action_name = _unique_name("uniquely_owned_action")
    first_extension = ExtensionModel(name=_unique_name("owner"), description="", schema_version=1)
    second_extension = ExtensionModel(name=_unique_name("owner"), description="", schema_version=1)
    db_session.add_all(
        [
            Action(name=action_name, description="", is_global=True),
            first_extension,
            second_extension,
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            ExtensionAction(action_name=action_name, extension_id=first_extension.id),
            ExtensionAction(action_name=action_name, extension_id=second_extension.id),
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


class TestExtensionsManagementRoutesAsAdmin:
    """Default fake_jwt_user is an admin — bypasses ABAC, exercises the route shapes."""

    async def test_register_list_get_replace_delete_round_trip(self, client):
        name = _unique_name("demo_ext")
        registration = _sample_registration(name)

        register_response = await client.post("/extensions", json=registration)
        assert register_response.status_code == 201
        assert register_response.json()["enabled"] is False

        list_response = await client.get("/extensions")
        assert list_response.status_code == 200
        assert any(ext["name"] == name for ext in list_response.json())

        get_response = await client.get(f"/extensions/{name}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == name

    async def test_get_returns_enrolled_action_descriptions(self, client):
        name = _unique_name("action_desc_ext")
        action_name = f"{name}.read"
        registration = _sample_registration(name)
        registration["actions"] = [{"name": action_name, "description": "Read widgets"}]

        register_response = await client.post("/extensions", json=registration)
        assert register_response.status_code == 201
        assert register_response.json()["actions"] == [
            {"name": action_name, "description": "Read widgets"}
        ]

        get_response = await client.get(f"/extensions/{name}")
        assert get_response.status_code == 200
        assert get_response.json()["actions"] == [
            {"name": action_name, "description": "Read widgets"}
        ]

        replacement = _sample_registration(name)
        replacement["description"] = "Updated description"
        replace_response = await client.put(f"/extensions/{name}", json=replacement)
        assert replace_response.status_code == 200
        assert replace_response.json()["description"] == "Updated description"
        # enabled must never change via PUT replace
        assert replace_response.json()["enabled"] is False

        delete_response = await client.delete(f"/extensions/{name}")
        assert delete_response.status_code == 204

        get_after_delete = await client.get(f"/extensions/{name}")
        assert get_after_delete.status_code == 404

    async def test_register_duplicate_name_conflicts(self, client):
        name = _unique_name("dup_ext")
        registration = _sample_registration(name)

        first = await client.post("/extensions", json=registration)
        assert first.status_code == 201

        second = await client.post("/extensions", json=registration)
        assert second.status_code == 409

    async def test_register_rejects_duplicate_action_names(self, client):
        name = _unique_name("duplicate_action_ext")
        registration = _sample_registration(name)
        registration["actions"] = [
            {"name": f"{name}.read", "description": "Read"},
            {"name": f"{name}.read", "description": "Read again"},
        ]

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 422
        assert "Duplicate action names" in response.json()["message"]

    async def test_register_returns_field_errors_for_invalid_manifest(self, client):
        registration = _sample_registration(_unique_name("invalid_manifest_ext"))
        registration["upstreams"]["svc"]["base_url"] = "ftp://not-allowed.example"

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 400
        assert response.json() == {
            "message": "Request validation failed",
            "errors": [
                {
                    "location": "upstreams.svc.http.base_url",
                    "message": "Value error, base_url must be an absolute HTTP or HTTPS URL",
                }
            ],
        }

    async def test_register_rejects_ownership_of_platform_action(self, client):
        name = _unique_name("platform_action_ext")
        registration = _sample_registration(name)
        registration["actions"] = [{"name": "device.read", "description": "Not extension-owned"}]

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 409
        assert "platform action" in response.json()["message"]

    async def test_register_rejects_unknown_required_action(self, client):
        name = _unique_name("unknown_action_ext")
        registration = _sample_registration(name)
        registration["routes"][0]["required_action"] = f"{name}.missing"

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 422
        assert "unknown action" in response.json()["message"]

    async def test_unknown_extension_returns_404(self, client):
        name = _unique_name("missing_ext")
        assert (await client.get(f"/extensions/{name}")).status_code == 404
        assert (await client.delete(f"/extensions/{name}")).status_code == 404
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 404
        assert (await client.post(f"/extensions/{name}/disable")).status_code == 404
        assert (await client.post(f"/extensions/{name}/internal-key/rotate")).status_code == 404

    async def test_enable_mounts_public_route_disable_unmounts_it(self, client):
        from main import app as public_app

        name = _unique_name("lifecycle_ext")
        await client.post("/extensions", json=_sample_registration(name))

        # freshly registered: inert, no live route yet
        assert not _live_route_names(public_app, name)

        enable_response = await client.post(f"/extensions/{name}/enable")
        assert enable_response.status_code == 200
        assert enable_response.json()["enabled"] is True
        assert len(_live_route_names(public_app, name)) == 1

        disable_response = await client.post(f"/extensions/{name}/disable")
        assert disable_response.status_code == 200
        assert disable_response.json()["enabled"] is False
        assert not _live_route_names(public_app, name)

        # disable never touches the persisted manifest - re-enabling brings the
        # same route back
        reenable_response = await client.post(f"/extensions/{name}/enable")
        assert reenable_response.status_code == 200
        assert len(_live_route_names(public_app, name)) == 1

        await client.delete(f"/extensions/{name}")
        assert not _live_route_names(public_app, name)

    async def test_enable_mounts_internal_route_on_internal_app(self, client):
        from main import app as public_app

        name = _unique_name("visibility_ext")
        registration = _sample_registration(name, visibility="public")
        registration["routes"].append(
            {
                "upstream": "svc",
                "path": f"/{name}/internal-ping",
                "method": "GET",
                "upstream_path": "/ping",
                "visibility": "internal",
            }
        )
        await client.post("/extensions", json=registration)
        await client.post(f"/extensions/{name}/enable")

        assert len(_live_route_names(public_app, name)) == 1
        assert len(_live_route_names(internal_app, name)) == 1

        await client.post(f"/extensions/{name}/disable")
        assert not _live_route_names(public_app, name)
        assert not _live_route_names(internal_app, name)

    async def test_rotate_internal_key_invalidates_previous_key(self, client):
        name = _unique_name("keyed_ext")
        await client.post("/extensions", json=_sample_registration(name))

        first = await client.post(f"/extensions/{name}/internal-key/rotate")
        assert first.status_code == 200
        first_key = first.json()["internal_key"]

        second = await client.post(f"/extensions/{name}/internal-key/rotate")
        assert second.status_code == 200
        second_key = second.json()["internal_key"]

        assert first_key != second_key

        current = await client.get(f"/extensions/{name}")
        # internal_key_hash is never exposed via GET
        assert "internal_key_hash" not in current.json()

    async def test_deregister_cleans_up_orphaned_actions(self, client, db_session):
        name = _unique_name("cleanup_ext")
        action_name = f"{name}.custom_action"
        registration = _sample_registration(name)
        registration["actions"] = [{"name": action_name, "description": "custom"}]

        await client.post("/extensions", json=registration)

        result = await db_session.execute(select(Action).where(Action.name == action_name))
        assert result.scalar_one_or_none() is not None

        await client.delete(f"/extensions/{name}")

        result = await db_session.execute(select(Action).where(Action.name == action_name))
        assert result.scalar_one_or_none() is None

    async def test_replace_refuses_to_drop_action_still_granted_to_a_role(self, client, db_session):
        name = _unique_name("guarded_ext")
        action_name = f"{name}.custom_action"
        registration = _sample_registration(name)
        registration["actions"] = [{"name": action_name, "description": "custom"}]

        await client.post("/extensions", json=registration)
        await AbacFixtures(db_session).setup(roles={"holder": [action_name]})

        replacement = _sample_registration(name)  # drops the action
        response = await client.put(f"/extensions/{name}", json=replacement)
        assert response.status_code == 409

    async def test_register_and_replace_with_new_required_action_on_a_route(self, client):
        """Regression test: a route's `required_action` FK depends on that action row
        already existing — actions must be enrolled before routes referencing them are
        persisted, on both register and replace (not just on register)."""
        name = _unique_name("required_action_ext")
        registration = _sample_registration(name)
        registration["actions"] = [{"name": f"{name}.read", "description": "read"}]
        registration["routes"][0]["required_action"] = f"{name}.read"

        register_response = await client.post("/extensions", json=registration)
        assert register_response.status_code == 201

        replacement = _sample_registration(name)
        replacement["actions"] = [{"name": f"{name}.write", "description": "write"}]
        replacement["routes"][0]["required_action"] = f"{name}.write"
        replace_response = await client.put(f"/extensions/{name}", json=replacement)
        assert replace_response.status_code == 200

    async def test_put_of_enabled_extension_remounts_live_routes(self, client):
        from main import app as public_app

        name = _unique_name("put_remount_ext")
        await client.post("/extensions", json=_sample_registration(name))
        await client.post(f"/extensions/{name}/enable")
        assert len(_live_route_names(public_app, name)) == 1

        replacement = _sample_registration(name)
        replacement["routes"][0]["path"] = f"/{name}/pong"
        replacement["routes"][0]["upstream_path"] = "/pong"
        replace_response = await client.put(f"/extensions/{name}", json=replacement)
        assert replace_response.status_code == 200
        assert replace_response.json()["enabled"] is True
        assert len(_live_route_names(public_app, name)) == 1
        live_paths = [
            getattr(r, "path", None)
            for r in public_app.router.routes
            if getattr(r, "name", "").startswith(f"extension_route__{name}__")
        ]
        assert f"/{name}/pong" in live_paths
        assert f"/{name}/ping" not in live_paths

        await client.delete(f"/extensions/{name}")

    async def test_deregister_strips_actions_from_roles(self, client, db_session):
        name = _unique_name("strip_ext")
        action_name = f"{name}.custom_action"
        registration = _sample_registration(name)
        registration["actions"] = [{"name": action_name, "description": "custom"}]
        await client.post("/extensions", json=registration)

        await AbacFixtures(db_session).setup(roles={"holder": [action_name]})

        await client.delete(f"/extensions/{name}")

        result = await db_session.execute(select(Action).where(Action.name == action_name))
        assert result.scalar_one_or_none() is None

    async def test_put_updates_action_description_and_scope_flag(self, client, db_session):
        name = _unique_name("action_update_ext")
        action_name = f"{name}.read"

        def manifest(description: str, scoped: bool) -> dict:
            registration = _sample_registration(name)
            registration["actions"] = [{"name": action_name, "description": description}]
            registration["routes"][0]["required_action"] = action_name
            registration["routes"][0]["scoped"] = scoped
            return registration

        assert (await client.post("/extensions", json=manifest("old", scoped=False))).status_code == 201
        action = (await db_session.execute(select(Action).where(Action.name == action_name))).scalar_one()
        assert action.is_global is True

        assert (await client.put(f"/extensions/{name}", json=manifest("new", scoped=True))).status_code == 200
        await db_session.refresh(action)
        assert action.description == "new"
        assert action.is_global is False

        await client.delete(f"/extensions/{name}")

    async def test_failed_deregister_rolls_back_role_grants(self, client, db_session, monkeypatch):
        from sqlalchemy import exists

        from db.models.role import role_actions
        from db.sqlalchemy.extension import SqlAlchemyExtensionRepository
        from exceptions import APIError

        name = _unique_name("atomic_delete_ext")
        action_name = f"{name}.custom_action"
        registration = _sample_registration(name)
        registration["actions"] = [{"name": action_name, "description": "custom"}]
        await client.post("/extensions", json=registration)
        await AbacFixtures(db_session).setup(roles={"holder": [action_name]})

        async def _fail(self, action_names):
            raise APIError("simulated failure after grants were stripped", 500)

        monkeypatch.setattr(SqlAlchemyExtensionRepository, "delete_orphaned_actions", _fail)
        assert (await client.delete(f"/extensions/{name}")).status_code == 500
        monkeypatch.undo()

        assert (await client.get(f"/extensions/{name}")).status_code == 200
        assert await db_session.scalar(select(exists().where(role_actions.c.action_name == action_name)))

        assert (await client.delete(f"/extensions/{name}")).status_code == 204


class TestExtensionRegistrationSchemaVersion:
    """Phase 6: `schema_version` is required on ExtensionRegistration, and
    `GET /extensions/schema` publishes its JSON Schema live."""

    async def test_schema_route_is_reachable_and_requires_schema_version(self, client):
        response = await client.get("/extensions/schema")
        assert response.status_code == 200
        schema = response.json()
        assert "schema_version" in schema["required"]
        assert schema["properties"]["schema_version"]["const"] == 1

    async def test_register_without_schema_version_is_never_persisted(self, client):
        name = _unique_name("no_schema_version_ext")
        registration = _sample_registration(name)
        del registration["schema_version"]

        response = await client.post("/extensions", json=registration)
        assert response.status_code != 201
        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_register_with_unrecognized_schema_version_is_never_persisted(self, client):
        name = _unique_name("bad_schema_version_ext")
        registration = _sample_registration(name)
        registration["schema_version"] = 2

        response = await client.post("/extensions", json=registration)
        assert response.status_code != 201
        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_register_and_get_and_replace_round_trip_schema_version(self, client):
        name = _unique_name("schema_version_ext")
        registration = _sample_registration(name)

        register_response = await client.post("/extensions", json=registration)
        assert register_response.status_code == 201
        assert register_response.json()["schema_version"] == 1

        get_response = await client.get(f"/extensions/{name}")
        assert get_response.json()["schema_version"] == 1

        replace_response = await client.put(f"/extensions/{name}", json=registration)
        assert replace_response.status_code == 200
        assert replace_response.json()["schema_version"] == 1


class TestExtensionsManagementRoutesAuthorization:
    """Requires extension.register|deregister|read — mirrors TestPlatformAuthorizationEndpoints."""

    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "extensions-test-oid",
            "sub": "extensions-test-oid",
            "preferred_username": "extensions-test@test.com",
            "name": "Extensions Test",
            "roles": [],
        }

    async def test_user_without_register_action_cannot_register(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": "extensions-test-oid"},
            roles={"device-reader": ["device.read"]},
            teams={"t": {"roles": ["device-reader"], "users": ["tester"]}},
        )

        response = await client.post("/extensions", json=_sample_registration(_unique_name("denied_ext")))
        assert response.status_code == 403

    async def test_user_with_register_action_can_register(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": "extensions-test-oid"},
            roles={"ext-registerer": ["extension.register"]},
            teams={"t": {"roles": ["ext-registerer"], "users": ["tester"]}},
        )

        response = await client.post("/extensions", json=_sample_registration(_unique_name("allowed_ext")))
        assert response.status_code == 201

    async def test_user_with_read_action_can_list_but_not_register(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": "extensions-test-oid"},
            roles={"ext-reader": ["extension.read"]},
            teams={"t": {"roles": ["ext-reader"], "users": ["tester"]}},
        )

        list_response = await client.get("/extensions")
        assert list_response.status_code == 200

        register_response = await client.post("/extensions", json=_sample_registration(_unique_name("blocked_ext")))
        assert register_response.status_code == 403

    async def test_user_without_deregister_action_cannot_disable_or_delete(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": "extensions-test-oid"},
            roles={"ext-registerer": ["extension.register"]},
            teams={"t": {"roles": ["ext-registerer"], "users": ["tester"]}},
        )

        name = _unique_name("undeletable_ext")
        register_response = await client.post("/extensions", json=_sample_registration(name))
        assert register_response.status_code == 201

        assert (await client.post(f"/extensions/{name}/disable")).status_code == 403
        assert (await client.delete(f"/extensions/{name}")).status_code == 403


WIDGET_CONFIG_REF = "#/components/schemas/WidgetConfigV1"
WIDGET_REPORTED_REF = "#/components/schemas/WidgetReportedV1"


def _widget_openapi_document() -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {},
        "components": {
            "schemas": {
                "WidgetConfigV1": {
                    "type": "object",
                    "required": ["interval"],
                    "properties": {"interval": {"type": "integer"}, "unit": {"$ref": "#/components/schemas/Unit"}},
                },
                "Unit": {"type": "string", "enum": ["ms", "s"]},
                "WidgetReportedV1": {"type": "object", "properties": {"config": {"type": "object"}}},
            }
        },
    }


class _OpenApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence default stderr access log
        pass

    def do_GET(self):
        document = self.server.document
        if self.path != "/openapi.json" or document is None:
            status, body = 404, b"{}"
        else:
            status, body = 200, json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def openapi_upstream():
    """A real HTTP upstream serving `server.document` at /openapi.json (None = unreachable)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenApiHandler)
    server.document = _widget_openapi_document()
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(autouse=True)
async def _fresh_http_upstream_client():
    """See test_extensions_dispatch.py: the shared httpx client must belong to this test's event loop."""
    previous = http_upstream._client
    http_upstream._client = httpx.AsyncClient()
    yield
    await http_upstream._client.aclose()
    http_upstream._client = previous


def _module_registration(name: str, module_id: str, base_url: str, reported: bool = True) -> dict:
    registration = _sample_registration(name)
    registration["upstreams"]["svc"]["base_url"] = base_url
    module = {"upstream": "svc", "schema_ref": WIDGET_CONFIG_REF}
    if reported:
        module["reported_schema_ref"] = WIDGET_REPORTED_REF
    registration["module_schemas"] = {module_id: module}
    return registration


class TestModuleSchemas:
    async def test_registration_persists_module_schemas_without_activating_them(self, client, openapi_upstream):
        name, module_id = _unique_name("module_ext"), _unique_name("widget-module")
        registration = _module_registration(name, module_id, openapi_upstream.base_url)

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 201
        assert response.json()["module_schemas"] == registration["module_schemas"]
        assert module_schema_registry.get(module_id, "configuration") is None
        assert (await client.get(f"/extensions/{name}")).json()["module_schemas"] == registration["module_schemas"]
        await client.delete(f"/extensions/{name}")

    async def test_invalid_module_schema_is_rejected_and_nothing_is_saved(self, client, openapi_upstream):
        name, module_id = _unique_name("bad_module_ext"), _unique_name("widget-module")
        registration = _module_registration(name, module_id, openapi_upstream.base_url)
        registration["module_schemas"][module_id]["schema_ref"] = "#/components/schemas/Missing"

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 422
        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_external_reference_in_module_schema_is_rejected(self, client, openapi_upstream):
        name, module_id = _unique_name("external_ref_ext"), _unique_name("widget-module")
        openapi_upstream.document["components"]["schemas"]["WidgetConfigV1"]["properties"]["unit"] = {
            "$ref": "https://elsewhere.example/unit.json"
        }
        registration = _module_registration(name, module_id, openapi_upstream.base_url)

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 422
        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_unreachable_module_upstream_is_rejected_and_nothing_is_saved(self, client, openapi_upstream):
        name, module_id = _unique_name("unreachable_module_ext"), _unique_name("widget-module")
        openapi_upstream.document = None
        registration = _module_registration(name, module_id, openapi_upstream.base_url)

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 502
        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_module_id_claimed_by_another_extension_conflicts_even_when_disabled(self, client, openapi_upstream):
        module_id = _unique_name("shared-module")
        first, second = _unique_name("owner_a"), _unique_name("owner_b")
        assert (await client.post("/extensions", json=_module_registration(first, module_id, openapi_upstream.base_url))).status_code == 201

        response = await client.post("/extensions", json=_module_registration(second, module_id, openapi_upstream.base_url))

        assert response.status_code == 409
        assert f"already claimed by extension '{first}'" in response.json()["message"]
        assert (await client.get(f"/extensions/{second}")).status_code == 404
        await client.delete(f"/extensions/{first}")

    async def test_enable_activates_and_disable_or_deregister_removes_module_schemas(self, client, openapi_upstream):
        name, module_id = _unique_name("lifecycle_module_ext"), _unique_name("widget-module")
        await client.post("/extensions", json=_module_registration(name, module_id, openapi_upstream.base_url))

        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
        configuration = module_schema_registry.get(module_id, "configuration")
        assert configuration is not None and configuration.extension_name == name
        assert module_schema_registry.get(module_id, "reported") is not None
        assert configuration.schema["components"]["schemas"]["Unit"] == {"type": "string", "enum": ["ms", "s"]}

        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
        assert module_schema_registry.get(module_id, "configuration") is None
        assert module_schema_registry.claim_owner(module_id) is None

        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
        assert module_schema_registry.get(module_id, "configuration") is not None

        assert (await client.delete(f"/extensions/{name}")).status_code == 204
        assert module_schema_registry.get(module_id, "configuration") is None

    async def test_replace_without_reported_ref_drops_reported_schema_and_keeps_the_core_route(self, client, openapi_upstream):
        from main import app as public_app

        name, module_id = _unique_name("drop_reported_ext"), _unique_name("widget-module")
        await client.post("/extensions", json=_module_registration(name, module_id, openapi_upstream.base_url))
        await client.post(f"/extensions/{name}/enable")
        assert module_schema_registry.get(module_id, "reported") is not None

        replacement = _module_registration(name, module_id, openapi_upstream.base_url, reported=False)
        response = await client.put(f"/extensions/{name}", json=replacement)

        assert response.status_code == 200
        assert module_schema_registry.get(module_id, "reported") is None
        assert module_schema_registry.get(module_id, "configuration") is not None
        assert len(_live_route_names(public_app, name)) == 1
        await client.delete(f"/extensions/{name}")

    async def test_failed_refresh_on_enable_keeps_the_last_valid_snapshot(self, client, openapi_upstream):
        name, module_id = _unique_name("refresh_module_ext"), _unique_name("widget-module")
        await client.post("/extensions", json=_module_registration(name, module_id, openapi_upstream.base_url))
        await client.post(f"/extensions/{name}/enable")
        last_valid = module_schema_registry.get(module_id, "configuration").version
        await client.post(f"/extensions/{name}/disable")

        openapi_upstream.document = None
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        assert module_schema_registry.get(module_id, "configuration").version == last_valid
        await client.delete(f"/extensions/{name}")

    async def test_startup_hydration_activates_persisted_module_schemas(self, client, db_session, openapi_upstream):
        name, module_id = _unique_name("hydrate_module_ext"), _unique_name("widget-module")
        await client.post("/extensions", json=_module_registration(name, module_id, openapi_upstream.base_url))
        await client.post(f"/extensions/{name}/enable")

        # Simulate a restarted process: the in-memory registry starts empty.
        module_schema_registry.replace_extension_entries(name, [], [])
        extension_repo = get_repository(ExtensionRepository)(db_session)
        await registry.refresh_ref_schemas(extension_repo)
        await _activate_enabled_module_schemas(extension_repo)

        assert module_schema_registry.get(module_id, "configuration") is not None
        assert module_schema_registry.get(module_id, "reported") is not None
        await client.delete(f"/extensions/{name}")

    async def test_hydrate_all_routes_activates_persisted_module_schemas_at_startup(
        self, client, db_session, openapi_upstream, monkeypatch
    ):
        name, module_id = _unique_name("startup_module_ext"), _unique_name("widget-module")
        await client.post("/extensions", json=_module_registration(name, module_id, openapi_upstream.base_url))
        await client.post(f"/extensions/{name}/enable")
        module_schema_registry.replace_extension_entries(name, [], [])

        # Run the real startup entrypoint against the test's isolated session, not its steps.
        @asynccontextmanager
        async def startup_session():
            yield db_session

        monkeypatch.setattr(extensions_setup, "AsyncSessionLocal", startup_session)
        await extensions_setup.hydrate_all_routes()

        assert module_schema_registry.get(module_id, "configuration") is not None
        assert module_schema_registry.get(module_id, "reported") is not None
        await client.delete(f"/extensions/{name}")

    async def test_enabled_module_with_an_invalid_stored_snapshot_stays_claimed_as_unavailable(self, client, db_session, openapi_upstream):
        name, module_id = _unique_name("unavailable_module_ext"), _unique_name("widget-module")
        await client.post("/extensions", json=_module_registration(name, module_id, openapi_upstream.base_url, reported=False))
        extension_repo = get_repository(ExtensionRepository)(db_session)
        stored = (await extension_repo.list_module_schemas(name))[0]
        await extension_repo.replace_module_schemas(
            name,
            [{**stored, "schema": {"type": "not-a-type"}}],
        )

        await _activate_module_schemas(extension_repo, name)

        assert module_schema_registry.get(module_id, "configuration") is None
        assert module_schema_registry.is_unavailable(module_id)
        assert module_schema_registry.claim_owner(module_id) == name
        module_schema_registry.replace_extension_entries(name, [], [])
