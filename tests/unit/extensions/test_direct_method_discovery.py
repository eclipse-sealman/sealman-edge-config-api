"""Source-named selection and authorization tests for extensions.discovery."""

import copy
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from db.repos.device import DeviceRepository
from db.repos.extension import ExtensionRepository
from db.repos.user import UserRepository
from extensions.discovery import list_direct_methods


@pytest.fixture
def repos():
    extension = AsyncMock(spec=ExtensionRepository)
    extension.get_extension_row.return_value = {"name": "neutral", "enabled": True, "internal_key_hash": "secret"}
    extension.list_upstreams.return_value = [
        {"key": "a", "type": "iotedge", "module_name": "module-a", "health_device_query": "private"},
        {"key": "b", "type": "iotedge", "module_name": "module-b"},
        {"key": "http", "type": "http", "base_url": "http://private", "connection_string": "secret"},
    ]
    extension.list_routes.return_value = []
    user = AsyncMock(spec=UserRepository)
    user.get_teams_with_roles_and_scopes.return_value = {"is_admin": True}
    device = AsyncMock(spec=DeviceRepository)
    device.get_device_meta_raw.return_value = {"region": "EU"}
    return SimpleNamespace(extension=extension, user=user, device=device)


def route(path="/neutral/call", **changes):
    return {
        "id": "route-1", "upstream": "a", "path": path, "method": "POST", "visibility": "public",
        "iotedge_operation": "direct_method", "method_name": "actualMethod",
        "required_action": "neutral.read", "scoped": True,
        "scope_param": "device_id", "scope_in": "query", **changes,
    }


async def discover(repos, auth=None):
    return await list_direct_methods(
        "neutral", "device-1", auth if auth is not None else {"sub": "caller"},
        repos.extension, repos.user, repos.device,
    )


async def test_groups_exact_module_and_method_retains_authorized_aliases_and_schemas(repos):
    body = {
        "$ref": "#/$defs/Payload", "$defs": {"Payload": {
            "type": "object", "properties": {"payload": {"type": "string"}}, "required": ["payload"],
        }},
    }
    repos.extension.list_routes.return_value = [
        route("/z", body=body, summary="Actual summary", description="Actual description", example={"password": "SECRET"}),
        route("/a/{target}", method="PUT", scope_param="target", scope_in="path", query_params=[
            {"name": "optional", "required": False, "type": "boolean"},
            {"name": "target", "required": True, "type": "string"},
        ]),
        route("/b", upstream="b"),
        route("/other", method_name="otherMethod"),
    ]
    original = copy.deepcopy(repos.extension.list_routes.return_value)
    result = await discover(repos)
    assert [(m.module_id, m.method_name) for m in result.methods] == [
        ("module-a", "actualMethod"), ("module-a", "otherMethod"), ("module-b", "actualMethod"),
    ]
    first, second = result.methods[0].invocations
    assert (first.path, first.http_method, first.scope_param, first.scope_in) == ("/a/{target}", "PUT", "target", "path")
    assert [(p.name, p.required) for p in first.query_params] == [("optional", False)]
    assert first.request_schema is None
    assert second.summary == "Actual summary" and second.description == "Actual description"
    assert second.request_schema == body
    assert [(p.name, p.required) for p in second.query_params] == [("device_id", True)]
    assert "SECRET" not in result.model_dump_json()
    assert not any(key in second.model_dump() for key in ("upstream", "example", "required_action", "internal_key_hash"))
    second.request_schema["$defs"]["Payload"]["required"].append("changed")
    assert repos.extension.list_routes.return_value == original
    repos.user.get_teams_with_roles_and_scopes.assert_awaited_once_with("caller")
    repos.device.get_device_meta_raw.assert_awaited_once_with("device-1")
    assert {call[0] for call in repos.extension.mock_calls} == {"get_extension_row", "list_upstreams", "list_routes"}


@pytest.mark.parametrize("changes", [
    {"visibility": "internal"}, {"upstream": "http"}, {"upstream": "missing"},
    {"iotedge_operation": "twin_read"}, {"iotedge_operation": "twin_write"},
    {"iotedge_operation": None}, {"method_name": None}, {"method_name": ""},
])
async def test_excludes_nonpublic_or_nondirect_bindings_even_for_admin(repos, changes):
    repos.extension.list_routes.return_value = [route(**changes)]
    assert (await discover(repos)).methods == []


@pytest.mark.parametrize("device_meta, expected", [({"region": "EU"}, ["global", "open", "scoped"]), ({"region": "US"}, ["global", "open"]), ({}, ["global", "open"])])
async def test_mirrors_scoped_unscoped_and_no_action_runtime_policy(repos, device_meta, expected):
    repos.user.get_teams_with_roles_and_scopes.return_value = {
        "teams": [{"roles": [{"allowed_actions": ["neutral.read"]}], "scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}}],
    }
    repos.device.get_device_meta_raw.return_value = device_meta
    repos.extension.list_routes.return_value = [
        route("/scoped", method_name="scoped"),
        route("/global", method_name="global", scoped=False),
        route("/open", method_name="open", required_action=None),
        route("/denied", method_name="denied", required_action="neutral.write"),
    ]
    result = await discover(repos)
    assert [m.method_name for m in result.methods] == expected
    global_call = result.methods[0].invocations[0]
    assert global_call.scoped is False and global_call.scope_param is None and global_call.scope_in is None
    assert [p.name for p in global_call.query_params] == ["device_id"]


async def test_disabled_returns_typed_empty_without_route_reads(repos):
    repos.extension.get_extension_row.return_value["enabled"] = False
    result = await discover(repos)
    assert result.model_dump() == {"extension": "neutral", "device_id": "device-1", "enabled": False, "methods": []}
    repos.extension.list_upstreams.assert_not_awaited()
    repos.extension.list_routes.assert_not_awaited()


@pytest.mark.parametrize("missing, code, message", [
    ("identifier", 403, "User identifier missing"), ("user", 403, "User not found"),
    ("device", 404, "Device 'device-1' not found"), ("extension", 404, "Extension 'neutral' not found"),
])
async def test_identity_and_resource_errors_fail_closed(repos, missing, code, message):
    auth = {} if missing == "identifier" else {"oid": "caller"}
    if missing == "user":
        repos.user.get_teams_with_roles_and_scopes.return_value = None
    if missing == "device":
        repos.device.get_device_meta_raw.return_value = None
    if missing == "extension":
        repos.extension.get_extension_row.return_value = None
    with pytest.raises(HTTPException) as error:
        await discover(repos, auth)
    assert error.value.status_code == code
    assert message in error.value.detail
    repos.extension.list_routes.assert_not_awaited()


async def test_no_cache_across_permission_device_or_manifest_changes(repos):
    repos.extension.list_routes.return_value = [route()]
    assert len((await discover(repos)).methods) == 1
    repos.user.get_teams_with_roles_and_scopes.return_value = {"teams": []}
    assert (await discover(repos)).methods == []
    repos.user.get_teams_with_roles_and_scopes.return_value = {"is_admin": True}
    repos.extension.list_routes.return_value = [route(method_name="replacement")]
    assert (await discover(repos)).methods[0].method_name == "replacement"
    repos.device.get_device_meta_raw.return_value = None
    with pytest.raises(HTTPException, match="not found"):
        await discover(repos)


@pytest.mark.parametrize("schema", [
    {"$ref": "#/$defs/missing"},
    {"$ref": "https://private.invalid/credentials?secret=DO-NOT-LOG"},
    {"$ref": "#missing-anchor"},
    {"type": "object", "properties": {"child": {"$dynamicRef": "https://private.invalid/dynamic"}}},
    {"$ref": "#/components/schemas/Payload", "components": {"schemas": {"Payload": {"$ref": "#/$defs/missing"}}}},
])
async def test_authorized_non_self_contained_body_is_safe_500_with_id_only_log(repos, schema, monkeypatch, caplog):
    from extensions.upstreams import http as http_upstream

    def forbidden(*args, **kwargs):
        raise AssertionError("No reference retrieval or network fetch is allowed")

    monkeypatch.setattr(http_upstream, "fetch_openapi_document", forbidden)
    monkeypatch.setattr(logging.getLogger("EdgeConfigAPI"), "propagate", True)
    repos.extension.list_routes.return_value = [route(body=schema)]
    with pytest.raises(HTTPException) as error:
        await discover(repos)
    assert error.value.status_code == 500
    assert error.value.detail == "This route's body schema is misconfigured"
    assert error.value.__suppress_context__ is True
    assert [record.getMessage() for record in caplog.records] == [
        "Direct-method body schema misconfigured: extension 'neutral', route 'route-1'"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "DO-NOT-LOG" not in caplog.text
    assert {call[0] for call in repos.extension.mock_calls} == {"get_extension_row", "list_upstreams", "list_routes"}


async def test_unauthorized_non_self_contained_body_never_triggers_safe_500(repos):
    repos.user.get_teams_with_roles_and_scopes.return_value = {"teams": []}
    repos.extension.list_routes.return_value = [route(body={"$ref": "https://private.invalid/secret"})]
    assert (await discover(repos)).methods == []


@pytest.mark.parametrize("schema", [
    {"$ref": "#/$defs/a~1b", "$defs": {"a/b": {"type": "string"}}},
    {"$ref": "#Payload", "$defs": {"Payload": {"$anchor": "Payload", "type": "string"}}},
    {"$ref": "#/$defs/Recursive", "$defs": {"Recursive": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Recursive"}}}}},
    {"$id": "https://schema.invalid/root", "$ref": "child", "$defs": {"Child": {"$id": "child", "type": "string"}}},
    {"type": "object", "properties": {"literal": {"type": "object", "examples": [{"$ref": "this is data, not a schema reference"}]}}},
])
async def test_locally_resolvable_body_preserves_refs_anchors_recursion_and_literal_data(repos, schema):
    repos.extension.list_routes.return_value = [route(body=schema)]
    assert (await discover(repos)).methods[0].invocations[0].request_schema == schema


async def test_query_projection_matches_first_declaration_used_by_invocation_signature(repos):
    repos.extension.list_routes.return_value = [route(query_params=[
        {"name": "limit", "type": "integer", "required": False, "description": "first"},
        {"name": "limit", "type": "string", "required": True, "description": "ignored duplicate"},
    ])]
    invocation = (await discover(repos)).methods[0].invocations[0]
    assert [(p.name, p.type, p.required) for p in invocation.query_params] == [
        ("device_id", "string", True), ("limit", "integer", False),
    ]
    assert invocation.query_params[1].description == "first"
