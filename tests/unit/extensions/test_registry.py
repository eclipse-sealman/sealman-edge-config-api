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
