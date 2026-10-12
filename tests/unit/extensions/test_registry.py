from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from exceptions import APIError
from extensions.registry import (
    _build_detail,
    _validate_action_ownership,
    _validate_manifest_cross_references,
)
from extensions.schemas import ExtensionRegistration
import extensions.schemas as schemas


def _registration(actions=None, required_action=None):
    return ExtensionRegistration.model_validate(
        {
            "schema_version": 1,
            "name": "widgets",
            "upstreams": {"svc": {"type": "http", "base_url": "https://widgets.example"}},
            "actions": actions or [],
            "routes": [
                {
                    "upstream": "svc",
                    "path": "/widgets",
                    "upstream_path": "/widgets",
                    "required_action": required_action,
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_build_detail_includes_action_descriptions():
    extension_repo = AsyncMock()
    extension_repo.get_extension_row.return_value = {
        "name": "widgets",
        "description": "Widget extension",
        "schema_version": 1,
        "enabled": False,
    }
    extension_repo.list_upstreams.return_value = []
    extension_repo.list_routes.return_value = []
    extension_repo.list_extension_action_specs.return_value = [
        {"name": "widgets.read", "description": "Read widgets"},
    ]

    detail = await _build_detail(extension_repo, "widgets")

    assert [action.model_dump() for action in detail.actions] == [
        {"name": "widgets.read", "description": "Read widgets"},
    ]
    extension_repo.list_extension_actions.assert_not_called()


def test_manifest_rejects_duplicate_action_names():
    registration = _registration(
        actions=[
            {"name": "widgets.read", "description": "Read"},
            {"name": "widgets.read", "description": "Read again"},
        ]
    )

    with pytest.raises(APIError, match="Duplicate action names") as exc_info:
        _validate_manifest_cross_references(registration)

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_action_declaration_rejects_platform_action():
    extension_repo = AsyncMock()
    extension_repo.get_action_owner.return_value = None
    extension_repo.action_exists.return_value = True

    with pytest.raises(APIError, match="platform action") as exc_info:
        await _validate_action_ownership(
            extension_repo,
            _registration(actions=[{"name": "device.read"}]),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_action_declaration_rejects_another_extensions_action():
    extension_repo = AsyncMock()
    extension_repo.get_action_owner.return_value = "catalog"

    with pytest.raises(APIError, match="owned by extension 'catalog'") as exc_info:
        await _validate_action_ownership(
            extension_repo,
            _registration(actions=[{"name": "catalog.read"}]),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_required_action_accepts_existing_platform_action():
    extension_repo = AsyncMock()
    extension_repo.get_action_owner.return_value = None
    extension_repo.action_exists.return_value = True

    await _validate_action_ownership(
        extension_repo,
        _registration(required_action="device.read"),
    )


@pytest.mark.asyncio
async def test_required_action_rejects_unknown_action():
    extension_repo = AsyncMock()
    extension_repo.get_action_owner.return_value = None
    extension_repo.action_exists.return_value = False

    with pytest.raises(APIError, match="unknown action") as exc_info:
        await _validate_action_ownership(
            extension_repo,
            _registration(required_action="missing.read"),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_replace_accepts_actions_already_owned_by_same_extension():
    extension_repo = AsyncMock()
    extension_repo.get_action_owner.return_value = "widgets"

    await _validate_action_ownership(
        extension_repo,
        _registration(actions=[{"name": "widgets.read"}], required_action="widgets.read"),
        current_extension="widgets",
    )


def test_public_route_without_required_action_is_allowed():
    _validate_manifest_cross_references(_registration())


def test_scoped_public_route_requires_required_action():
    with pytest.raises(APIError, match="must set required_action") as exc_info:
        _validate_manifest_cross_references(_iotedge_registration(scoped=True, required_action=None))
    assert exc_info.value.status_code == 422


def test_scoped_query_parameter_must_be_required():
    registration = _iotedge_registration(
        scoped=True,
        query_params=[{"name": "device_id", "required": False}],
    )

    with pytest.raises(APIError, match="query scope parameter 'device_id' must be required"):
        _validate_manifest_cross_references(registration)


def _iotedge_registration(**route_overrides):
    route = {
        "upstream": "mod",
        "path": "/restart",
        "method": "POST",
        "required_action": "device.module.execute_method",
        "iotedge": {"operation": "direct_method", "method_name": "restart"},
        **route_overrides,
    }
    return ExtensionRegistration.model_validate(
        {
            "schema_version": 1,
            "name": "widgets",
            "upstreams": {"mod": {"type": "iotedge", "module_name": "widget-module"}},
            "routes": [route],
        }
    )


def test_public_iotedge_route_must_be_scoped():
    with pytest.raises(APIError, match="must be scoped") as exc_info:
        _validate_manifest_cross_references(_iotedge_registration())
    assert exc_info.value.status_code == 422

    _validate_manifest_cross_references(_iotedge_registration(scoped=True))


def test_internal_iotedge_route_may_be_unscoped():
    _validate_manifest_cross_references(_iotedge_registration(visibility="internal", required_action=None))


def test_http_upstream_requires_an_allowlisted_host_and_port(monkeypatch):
    monkeypatch.setattr(schemas, "EXTENSIONS_HTTP_UPSTREAM_ALLOWLIST", "localhost:*")

    _registration_with_upstream("http://localhost:9000")

    with pytest.raises(ValidationError, match="not in EXTENSIONS_HTTP_UPSTREAM_ALLOWLIST"):
        _registration_with_upstream("https://widgets.example")


def _registration_with_upstream(base_url):
    return ExtensionRegistration.model_validate(
        {
            "schema_version": 1,
            "name": "widgets",
            "upstreams": {"svc": {"type": "http", "base_url": base_url}},
            "routes": [],
        }
    )


def test_registration_schema_does_not_advertise_server_computed_fields():
    schema = ExtensionRegistration.model_json_schema()
    definitions = schema["$defs"]
    for model in ("HttpUpstreamSpec", "IotedgeUpstreamSpec"):
        assert not {"last_status", "last_checked_at", "last_detail"} & set(definitions[model]["properties"])
    assert "validation_mode" not in definitions["RouteSpec"]["properties"]


def test_action_is_global_unless_every_public_route_using_it_is_scoped():
    from extensions.registry import _action_is_global
    from extensions.schemas import RouteSpec

    def route(**overrides):
        return RouteSpec.model_validate(
            {"upstream": "svc", "path": "/a", "upstream_path": "/a", "required_action": "w.read", **overrides}
        )

    assert _action_is_global("w.read", []) is True
    assert _action_is_global("w.read", [route(scoped=True)]) is False
    assert _action_is_global("w.read", [route(scoped=True), route(scoped=False)]) is True
    assert _action_is_global("w.read", [route(scoped=True), route(visibility="internal")]) is False


@pytest.mark.parametrize("module_name", ["$edgeAgent", "$edgeHub"])
def test_iotedge_upstream_rejects_system_modules(module_name):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="system modules"):
        ExtensionRegistration.model_validate(
            {
                "schema_version": 1,
                "name": "widgets",
                "upstreams": {"mod": {"type": "iotedge", "module_name": module_name}},
                "routes": [],
            }
        )


@pytest.mark.parametrize("path", ["/a/{id:int}", "/a/{class}", "/a/{bad-name}", "/a/{id", "/a/id}"])
def test_manifest_rejects_unmountable_path_placeholders(path):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExtensionRegistration.model_validate(
            {
                "schema_version": 1,
                "name": "widgets",
                "upstreams": {"svc": {"type": "http", "base_url": "https://widgets.example"}},
                "routes": [{"upstream": "svc", "path": path, "upstream_path": "/x", "required_action": "a"}],
            }
        )


def _module_registration(module_schemas=None, reported=True, upstream_type="http"):
    upstream = (
        {"type": "http", "base_url": "https://widgets.example"}
        if upstream_type == "http"
        else {"type": "iotedge", "module_name": "widget-module"}
    )
    spec = {"upstream": "svc", "schema_ref": "#/components/schemas/WidgetConfigV1"}
    if reported:
        spec["reported_schema_ref"] = "#/components/schemas/WidgetReportedV1"
    return ExtensionRegistration.model_validate(
        {
            "schema_version": 1,
            "name": "widgets",
            "upstreams": {"svc": upstream},
            "routes": [],
            "module_schemas": module_schemas if module_schemas is not None else {"widget-module-1": spec},
        }
    )


WIDGET_OPENAPI = {
    "openapi": "3.1.0",
    "paths": {},
    "components": {
        "schemas": {
            "WidgetConfigV1": {
                "type": "object",
                "required": ["interval"],
                "properties": {"interval": {"type": "integer"}, "nested": {"$ref": "#/components/schemas/Nested"}},
            },
            "Nested": {"type": "string"},
            "WidgetReportedV1": {"type": "object", "properties": {"config": {"type": "object"}}},
        }
    },
}


def _repo_with_atomic():
    from contextlib import asynccontextmanager

    repo = AsyncMock()

    @asynccontextmanager
    async def atomic():
        yield

    repo.atomic = atomic
    repo.extension_exists.return_value = False
    repo.get_module_schema_owner.return_value = None
    repo.list_module_schemas.return_value = []
    return repo


def test_module_schema_fields_are_optional_and_validated_against_upstreams():
    assert _registration().module_schemas == {}
    _validate_manifest_cross_references(_module_registration())

    with pytest.raises(APIError, match="unknown upstream 'missing'") as exc_info:
        _validate_manifest_cross_references(
            _module_registration({"widget-module-1": {"upstream": "missing", "schema_ref": "#/components/schemas/A"}})
        )
    assert exc_info.value.status_code == 422

    with pytest.raises(APIError, match="requires an http upstream"):
        _validate_manifest_cross_references(_module_registration(upstream_type="iotedge"))


def test_response_ref_requires_an_http_upstream():
    registration = _iotedge_registration(response_ref=True, visibility="internal", required_action=None)
    with pytest.raises(APIError, match="sets response_ref"):
        _validate_manifest_cross_references(registration)


def test_module_schema_rows_resolve_configuration_and_reported_contracts():
    from extensions.registry import _module_schema_rows

    registration = _module_registration()
    rows = _module_schema_rows(
        registration.module_schemas,
        registration.upstreams,
        {"https://widgets.example": WIDGET_OPENAPI},
    )

    assert [(row["module_id"], row["purpose"], row["schema_ref"]) for row in rows] == [
        ("widget-module-1", "configuration", "#/components/schemas/WidgetConfigV1"),
        ("widget-module-1", "reported", "#/components/schemas/WidgetReportedV1"),
    ]
    assert set(rows[0]["schema"]["components"]["schemas"]) == {"WidgetConfigV1", "Nested"}
    assert "components" not in rows[1]["schema"] or set(rows[1]["schema"]["components"]["schemas"]) == {"WidgetReportedV1"}
    assert all(len(row["version"]) == 64 for row in rows)


@pytest.mark.parametrize(
    "document, status_code, message",
    [
        (None, 502, "unreachable"),
        ({"components": {"schemas": {}}}, 422, "Unresolved component"),
        (
            {"components": {"schemas": {"WidgetConfigV1": {"type": "object", "properties": {"x": {"type": "not-a-type"}}}}}},
            422,
            "not a valid JSON Schema",
        ),
    ],
)
def test_module_schema_failures_map_to_502_or_422(document, status_code, message):
    from extensions.registry import _module_schema_rows

    registration = _module_registration(reported=False)
    with pytest.raises(APIError, match=message) as exc_info:
        _module_schema_rows(registration.module_schemas, registration.upstreams, {"https://widgets.example": document})
    assert exc_info.value.status_code == status_code


@pytest.mark.asyncio
async def test_register_with_unreachable_module_schema_saves_nothing(monkeypatch):
    from extensions import registry

    async def unreachable(_base_url):
        return None

    monkeypatch.setattr(registry.http_upstream, "fetch_openapi_document", unreachable)
    repo = _repo_with_atomic()

    with pytest.raises(APIError) as exc_info:
        await registry.register_extension(repo, _module_registration())

    assert exc_info.value.status_code == 502
    repo.create_extension.assert_not_called()
    repo.replace_module_schemas.assert_not_called()


@pytest.mark.asyncio
async def test_register_rejects_module_claimed_by_another_extension(monkeypatch):
    from extensions import registry

    async def fetch(_base_url):
        return WIDGET_OPENAPI

    monkeypatch.setattr(registry.http_upstream, "fetch_openapi_document", fetch)
    repo = _repo_with_atomic()
    repo.get_module_schema_owner.return_value = "other-extension"

    with pytest.raises(APIError, match="already claimed by extension 'other-extension'") as exc_info:
        await registry.register_extension(repo, _module_registration())

    assert exc_info.value.status_code == 409
    repo.create_extension.assert_not_called()
    repo.replace_module_schemas.assert_not_called()


@pytest.mark.asyncio
async def test_register_persists_resolved_module_rows(monkeypatch):
    from extensions import registry

    async def fetch(_base_url):
        return WIDGET_OPENAPI

    monkeypatch.setattr(registry.http_upstream, "fetch_openapi_document", fetch)
    repo = _repo_with_atomic()
    repo.get_extension_row.return_value = {"name": "widgets", "description": "", "schema_version": 1, "enabled": False}
    repo.list_upstreams.return_value = []
    repo.list_routes.return_value = []
    repo.list_extension_action_specs.return_value = []
    repo.list_module_schemas.return_value = []

    await registry.register_extension(repo, _module_registration())

    rows = repo.replace_module_schemas.call_args.args[1]
    assert [(row["module_id"], row["purpose"]) for row in rows] == [
        ("widget-module-1", "configuration"),
        ("widget-module-1", "reported"),
    ]


@pytest.mark.asyncio
async def test_failed_refresh_keeps_the_last_valid_module_snapshot(monkeypatch):
    from extensions import registry

    stored = {
        "module_id": "widget-module-1",
        "purpose": "configuration",
        "upstream": "svc",
        "schema_ref": "#/components/schemas/WidgetConfigV1",
        "schema": {"$ref": "#/components/schemas/WidgetConfigV1", "components": {"schemas": {"WidgetConfigV1": {"type": "object"}}}},
        "version": "old",
    }
    repo = _repo_with_atomic()
    repo.get_extension_row.return_value = {"name": "widgets"}
    repo.list_module_schemas.return_value = [stored]
    repo.list_upstreams.return_value = [{"key": "svc", "type": "http", "base_url": "https://widgets.example"}]
    repo.list_routes.return_value = []

    async def unreachable(_base_url):
        return None

    monkeypatch.setattr(registry.http_upstream, "fetch_openapi_document", unreachable)
    await registry.refresh_extension_ref_schemas(repo, "widgets")
    repo.replace_module_schemas.assert_not_called()

    async def reachable(_base_url):
        return WIDGET_OPENAPI

    monkeypatch.setattr(registry.http_upstream, "fetch_openapi_document", reachable)
    await registry.refresh_extension_ref_schemas(repo, "widgets")
    refreshed = repo.replace_module_schemas.call_args.args[1]
    assert refreshed[0]["version"] != "old"
    assert refreshed[0]["module_id"] == "widget-module-1"


@pytest.mark.asyncio
async def test_load_entries_activates_only_valid_module_snapshots():
    from extensions.registry import load_module_schema_entries

    valid_config = {"module_id": "m-ok", "purpose": "configuration", "schema": {"type": "object"}, "version": "v1"}
    valid_reported = {"module_id": "m-ok", "purpose": "reported", "schema": {"type": "object"}, "version": "v2"}
    invalid_config = {"module_id": "m-bad", "purpose": "configuration", "schema": {"type": "bogus"}, "version": "v3"}
    reported_only = {"module_id": "m-orphan", "purpose": "reported", "schema": {"type": "object"}, "version": "v4"}
    repo = AsyncMock()
    repo.list_module_schemas.return_value = [valid_config, valid_reported, invalid_config, reported_only]

    active, unavailable = await load_module_schema_entries(repo, "widgets")

    assert sorted((entry.module_id, entry.purpose, entry.version) for entry in active) == [
        ("m-ok", "configuration", "v1"),
        ("m-ok", "reported", "v2"),
    ]
    assert sorted(unavailable) == ["m-bad", "m-orphan"]


@pytest.mark.asyncio
async def test_check_module_schema_ownership_rejects_another_owner():
    from extensions.registry import check_module_schema_ownership

    repo = AsyncMock()
    repo.list_module_schemas.return_value = [{"module_id": "widget-module-1"}]
    repo.get_module_schema_owner.return_value = "other-extension"

    with pytest.raises(APIError) as exc_info:
        await check_module_schema_ownership(repo, "widgets")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_build_detail_reports_declared_module_schemas_and_hides_response_snapshot():
    repo = AsyncMock()
    repo.get_extension_row.return_value = {"name": "widgets", "description": "", "schema_version": 1, "enabled": True}
    repo.list_upstreams.return_value = [{"id": "u", "key": "svc", "type": "http", "base_url": "https://widgets.example"}]
    repo.list_routes.return_value = [
        {"id": "r", "upstream": "svc", "path": "/w", "method": "GET", "response_ref": True, "response_schema": {"type": "object"}}
    ]
    repo.list_extension_action_specs.return_value = []
    repo.list_module_schemas.return_value = [
        {"module_id": "widget-module-1", "purpose": "configuration", "upstream": "svc", "schema_ref": "#/components/schemas/A"},
        {"module_id": "widget-module-1", "purpose": "reported", "upstream": "svc", "schema_ref": "#/components/schemas/B"},
    ]

    detail = await _build_detail(repo, "widgets")

    assert detail.module_schemas["widget-module-1"].model_dump() == {
        "upstream": "svc",
        "schema_ref": "#/components/schemas/A",
        "reported_schema_ref": "#/components/schemas/B",
    }
    assert detail.routes[0].response_ref is True
    assert "response_schema" not in detail.routes[0].model_dump()
