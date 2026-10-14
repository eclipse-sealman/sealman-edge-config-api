"""Public persisted discovery through real lifecycle, SQL repositories and ABAC."""

import copy
import logging
from contextlib import contextmanager

import pytest

import auth
from db.repos.device import DeviceRepository
from db.repos.role import RoleRepository
from db.session import get_repository
from extensions import registry, runtime, setup as extensions_setup
from extensions.upstreams import http as http_upstream, iotedge as iotedge_upstream
from main import app
from routers.module_config.routes import (
    get_module_twin_config as read_route,
    get_module_twin_identity_reported as reported_route,
    post_module_twin_config as write_route,
)
from tests.integration.test_abac_authorization import AbacFixtures
from tests.integration.test_extensions_management_routes import _unique_name
from tests.integration.test_module_schema_lifecycle import hydrate, lifecycle_state  # noqa: F401

DEVICE = "discovery-eu"
OTHER_DEVICE = "discovery-us"
STATIC_PATH = "/extensions/{name}/direct-methods"


@pytest.fixture
def forbid_discovery_io(monkeypatch):
    @contextmanager
    def guard():
        def forbidden(*args, **kwargs):
            raise AssertionError("Discovery must not dispatch, fetch schemas, probe health or access cloud storage/twins")

        with monkeypatch.context() as patch:
            for kind in runtime._DISPATCHERS:
                patch.setitem(runtime._DISPATCHERS, kind, forbidden)
            for module, names in (
                (iotedge_upstream, ("dispatch", "_direct_method", "_twin_read", "_twin_write", "check_module_health", "probe_registration_health", "get_async", "post_async", "patch_async")),
                (http_upstream, ("dispatch", "fetch_openapi_document")),
                (registry, ("_fetch_openapi_documents", "check_extension_health", "probe_registration_health")),
                (read_route, ("BlobContainerContext", "get_async")),
                (write_route, ("BlobContainerContext", "patch_async")),
                (reported_route, ("get_async",)),
            ):
                for name in names:
                    patch.setattr(module, name, forbidden)
            yield
    return guard


def manifest(name):
    action = f"{name}.read"

    def call(path, **changes):
        return {
            "upstream": "a", "path": f"/{name}/{path}", "method": "POST",
            "iotedge": {"operation": "direct_method", "method_name": "actualMethod"},
            "required_action": action, "scoped": True, **changes,
        }

    return {
        "schema_version": 1, "name": name, "actions": [{"name": action}],
        "upstreams": {
            "a": {"type": "iotedge", "module_name": "module-a"},
            "b": {"type": "iotedge", "module_name": "module-b"},
            "svc": {"type": "http", "base_url": "http://localhost:9000"},
        },
        "routes": [
            call("scoped", body={"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}, example={"value": 123}),
            call("alias/{target}", method="PUT", scope_param="target", scope_in="path", query_params=[{"name": "limit", "type": "integer", "required": False}]),
            call("other-module", upstream="b"),
            call("internal", visibility="internal", required_action=None, iotedge={"method_name": "internalMethod"}),
            call("twin-read", iotedge={"operation": "twin_read"}),
            call("twin-write", iotedge={"operation": "twin_write"}),
            {"upstream": "svc", "path": f"/{name}/http", "method": "GET", "upstream_path": "/private"},
        ],
    }


async def register(client, owned, name, enabled=True):
    owned.append(name)
    registration = manifest(name)
    response = await client.post("/extensions", json=registration)
    assert response.status_code == 201, response.text
    if enabled:
        response = await client.post(f"/extensions/{name}/enable")
        assert response.status_code == 200, response.text
    return registration


async def world(db_session, name):
    return await AbacFixtures(db_session).setup(
        users={"reader": "discovery-reader", "none": "discovery-none"},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"reader": [f"{name}.read"]},
        teams={"reader": {"scope": "eu", "roles": ["reader"], "users": ["reader"]}},
        devices={DEVICE: {"region": "EU"}, OTHER_DEVICE: {"region": "US"}},
    )


def caller(fake_jwt_user, oid="discovery-reader"):
    fake_jwt_user.clear()
    fake_jwt_user.update({"oid": oid, "name": "Discovery caller", "roles": []})


async def catalog(client, name, device=DEVICE):
    response = await client.get(f"/extensions/{name}/direct-methods", params={"device_id": device})
    assert response.status_code == 200, response.text
    return response.json()


async def test_real_nonadmin_scoped_projection_and_no_crosscaller_cache(
    client, db_session, fake_jwt_user, lifecycle_state, forbid_discovery_io,
):
    name = _unique_name("discovery")
    await register(client, lifecycle_state, name)
    await world(db_session, name)
    caller(fake_jwt_user)
    assert (await client.get(f"/extensions/{name}")).status_code == 403  # No Extension.READ.
    with forbid_discovery_io():
        eu = await catalog(client, name)
        assert [(m["module_id"], m["method_name"]) for m in eu["methods"]] == [
            ("module-a", "actualMethod"), ("module-b", "actualMethod"),
        ]
        alias, scoped = eu["methods"][0]["invocations"]
        assert alias["path"].endswith("/alias/{target}") and alias["http_method"] == "PUT"
        assert (alias["scope_param"], alias["scope_in"]) == ("target", "path")
        assert alias["query_params"] == [{"name": "limit", "type": "integer", "required": False, "description": None}]
        assert scoped["request_schema"]["required"] == ["value"]
        assert [p["name"] for p in scoped["query_params"]] == ["device_id"]
        assert eu["methods"][1]["invocations"][0]["request_schema"] is None
        text = str(eu)
        assert not any(value in text for value in ("base_url", "localhost", "upstream", "internalMethod", "example", "twin-read", "twin-write"))
        us = await catalog(client, name, OTHER_DEVICE)
        assert us["methods"] == []
        caller(fake_jwt_user, "discovery-none")
        assert (await catalog(client, name))["methods"] == []


async def test_role_and_device_metadata_changes_are_visible_without_reenable(
    client, db_session, fake_jwt_user, lifecycle_state, forbid_discovery_io,
):
    name = _unique_name("discovery_changes")
    await register(client, lifecycle_state, name)
    seeded = await world(db_session, name)
    caller(fake_jwt_user)
    with forbid_discovery_io():
        assert len((await catalog(client, name))["methods"]) == 2
        devices = get_repository(DeviceRepository)(db_session)
        await devices.update_device_metadata(DEVICE, {"region": "US"})
        assert (await catalog(client, name))["methods"] == []
        roles = get_repository(RoleRepository)(db_session)
        await roles.remove_action_from_role(seeded.roles["reader"], f"{name}.read")
        assert (await catalog(client, name))["methods"] == []
        await roles.add_actions_to_role(seeded.roles["reader"], [f"{name}.read"])
        await devices.update_device_metadata(DEVICE, {"region": "EU"})
        assert len((await catalog(client, name))["methods"]) == 2


async def test_disabled_empty_replace_deregister_hydration_and_static_openapi(
    client, db_session, lifecycle_state, hydrate, forbid_discovery_io,
):
    name = _unique_name("discovery_lifecycle")
    registration = await register(client, lifecycle_state, name, enabled=False)
    await world(db_session, name)
    baseline = (await client.get("/openapi.json")).json()
    operation = baseline["paths"][STATIC_PATH]["get"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/DirectMethodDiscovery"}
    assert operation["security"] and operation["parameters"][1]["required"]
    with forbid_discovery_io():
        assert await catalog(client, name) == {"extension": name, "device_id": DEVICE, "enabled": False, "methods": []}
    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    with forbid_discovery_io():
        original = await catalog(client, name)
    # The startup entrypoint is exercised explicitly; ASGITransport has no lifespan.
    extensions_setup._unmount_extension(name)
    await hydrate()
    with forbid_discovery_io():
        assert await catalog(client, name) == original
    replacement = copy.deepcopy(registration)
    replacement["routes"] = [replacement["routes"][0]]
    replacement["routes"][0]["iotedge"]["method_name"] = "replacementMethod"
    replacement["routes"][0]["body"] = {"type": "object", "properties": {"changed": {"type": "boolean"}}}
    replacement["routes"][0].pop("example")
    assert (await client.put(f"/extensions/{name}", json=replacement)).status_code == 200
    with forbid_discovery_io():
        updated = await catalog(client, name)
        assert [m["method_name"] for m in updated["methods"]] == ["replacementMethod"]
        assert updated["methods"][0]["invocations"][0]["request_schema"] == replacement["routes"][0]["body"]
    replacement["routes"] = []
    assert (await client.put(f"/extensions/{name}", json=replacement)).status_code == 200
    with forbid_discovery_io():
        assert (await catalog(client, name))["methods"] == []
    assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
    assert (await client.delete(f"/extensions/{name}")).status_code == 204
    with forbid_discovery_io():
        assert (await client.get(f"/extensions/{name}/direct-methods", params={"device_id": DEVICE})).status_code == 404
    document = (await client.get("/openapi.json")).json()
    assert document["paths"][STATIC_PATH]["get"] == operation
    ids = [item["operationId"] for path in document["paths"].values() for item in path.values() if "operationId" in item]
    assert len(ids) == len(set(ids))
    assert sum(getattr(route, "path", None) == STATIC_PATH for route in app.routes) == 1


@pytest.mark.parametrize("query, status", [
    ({}, 400), ({"device_id": ""}, 400), ({"device_id": "unknown"}, 404),
    ([("device_id", DEVICE), ("device_id", OTHER_DEVICE)], 400),
])
async def test_device_validation(client, db_session, lifecycle_state, query, status, forbid_discovery_io):
    name = _unique_name("discovery_validation")
    await register(client, lifecycle_state, name, enabled=False)
    await world(db_session, name)
    with forbid_discovery_io():
        response = await client.get(f"/extensions/{name}/direct-methods", params=query)
    assert response.status_code == status, response.text


async def test_unknown_extension(client, db_session, forbid_discovery_io):
    await AbacFixtures(db_session).setup(devices={DEVICE: {"region": "EU"}})
    with forbid_discovery_io():
        assert (await client.get("/extensions/unregistered/direct-methods", params={"device_id": DEVICE})).status_code == 404


@pytest.mark.parametrize("token", [None, "invalid-synthetic-token"])
async def test_missing_or_invalid_public_auth_stays_401(client, monkeypatch, token):
    app.dependency_overrides.pop(auth.validate_jwt)

    async def initialized():
        pass

    def invalid(*args):
        raise ValueError("synthetic invalid JWT")

    monkeypatch.setattr(auth, "ensure_oidc_initialized", initialized)
    monkeypatch.setattr(auth, "verify_token", invalid)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = await client.get("/extensions/unregistered/direct-methods", params={"device_id": DEVICE}, headers=headers)
    assert response.status_code == 401, response.text


async def test_missing_identifier_keeps_global_401(client, fake_jwt_user):
    fake_jwt_user.clear()
    fake_jwt_user.update({"name": "Missing identifier"})
    response = await client.get("/extensions/unregistered/direct-methods", params={"device_id": DEVICE})
    assert response.status_code == 401


async def test_unresolved_user_is_403_after_test_only_provisioning_override(client, fake_jwt_user):
    caller(fake_jwt_user, "not-persisted")

    async def skip_provisioning():
        return fake_jwt_user

    app.dependency_overrides[auth.ensure_user_exists] = skip_provisioning
    response = await client.get("/extensions/unregistered/direct-methods", params={"device_id": DEVICE})
    assert response.status_code == 403, response.text
    assert "User not found" in response.json()["message"]


@pytest.mark.parametrize("reference", ["https://private.invalid/DO-NOT-LOG", "#/$defs/missing"])
async def test_authorized_non_self_contained_body_exact_safe_500_without_fetch_or_unauthorized_effect(
    client, db_session, fake_jwt_user, lifecycle_state, forbid_discovery_io, reference, caplog, monkeypatch,
):
    from db.repos.extension import ExtensionRepository

    monkeypatch.setattr(logging.getLogger("EdgeConfigAPI"), "propagate", True)
    name = _unique_name("discovery_bad_schema")
    registration = await register(client, lifecycle_state, name)
    await world(db_session, name)
    bad_body = {"$ref": reference, "description": "SCHEMA-CONTENT-DO-NOT-LOG"}
    registration["routes"][0]["body"] = bad_body
    registration["routes"][0].pop("example")
    assert (await client.put(f"/extensions/{name}", json=registration)).status_code == 200
    repo = get_repository(ExtensionRepository)(db_session)
    rows = await repo.list_routes(name)
    route_id = next(row["id"] for row in rows if row["body"] == bad_body)
    caller(fake_jwt_user, "discovery-none")
    caplog.clear()
    with forbid_discovery_io():
        assert (await catalog(client, name))["methods"] == []
        caller(fake_jwt_user)
        assert (await catalog(client, name, OTHER_DEVICE))["methods"] == []
        response = await client.get(f"/extensions/{name}/direct-methods", params={"device_id": DEVICE})
    assert response.status_code == 500
    assert response.json() == {"message": "This route's body schema is misconfigured"}
    assert not any(text in response.text for text in (str(route_id), reference, "SCHEMA-CONTENT", "Traceback", "Unresolvable"))
    discovery_logs = [record for record in caplog.records if record.getMessage().startswith("Direct-method body schema")]
    assert len(discovery_logs) == 1
    assert discovery_logs[0].getMessage() == f"Direct-method body schema misconfigured: extension '{name}', route '{route_id}'"
    assert discovery_logs[0].exc_info is None
    assert reference not in caplog.text and "SCHEMA-CONTENT" not in caplog.text
    # Disable is management-only; the subsequent listing stays normal user JWT.
    fake_jwt_user.clear()
    fake_jwt_user.update({"oid": "test-admin-oid", "roles": ["user.admin"]})
    assert (await client.post(f"/extensions/{name}/disable")).status_code == 200
    caller(fake_jwt_user)
    caplog.clear()
    with forbid_discovery_io():
        assert await catalog(client, name) == {"extension": name, "device_id": DEVICE, "enabled": False, "methods": []}
    assert not any(record.getMessage().startswith("Direct-method body schema") for record in caplog.records)


async def test_locally_resolvable_body_returned_exactly_despite_other_extension_bad_body(
    client, db_session, lifecycle_state, forbid_discovery_io,
):
    bad_name = _unique_name("discovery_other_bad")
    bad = await register(client, lifecycle_state, bad_name)
    bad["routes"][0]["body"] = {"$ref": "https://private.invalid/DO-NOT-LOG"}
    bad["routes"][0].pop("example")
    assert (await client.put(f"/extensions/{bad_name}", json=bad)).status_code == 200
    name = _unique_name("discovery_local_schema")
    registration = await register(client, lifecycle_state, name)
    await world(db_session, name)
    body = {
        "$ref": "#/$defs/Payload",
        "$defs": {"Payload": {
            "$anchor": "Payload", "type": "object",
            "properties": {"child": {"$ref": "#Payload"}, "value": {"type": "string"}},
        }},
    }
    registration["routes"][0]["body"] = body
    registration["routes"][0].pop("example")
    assert (await client.put(f"/extensions/{name}", json=registration)).status_code == 200
    with forbid_discovery_io():
        result = await catalog(client, name)
    returned = next(invocation["request_schema"] for method in result["methods"] for invocation in method["invocations"] if invocation["path"].endswith("/scoped"))
    assert returned == body
