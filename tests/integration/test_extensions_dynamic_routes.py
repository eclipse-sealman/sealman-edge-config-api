"""Integration coverage for dynamic route creation & request body handling: route
metadata surfaced in /openapi.json, operation_id uniqueness, `declared`-mode JSON
Schema body validation end-to-end through a mounted route, `example`/`body` pairing
rejected at registration, and `body_ref` (`upstream_declared`/`unreachable_ref`)
flipping on a simulated restart.
"""
from typing import AsyncGenerator, Generator

import httpx
import pytest

from db.repos.extension import ExtensionRepository
from db.session import get_repository
from extensions import registry
from extensions.upstreams import http as http_upstream
from tests.integration.test_extensions_dispatch import _EchoHandler, mock_http_upstream  # noqa: F401
from tests.integration.test_extensions_management_routes import _unique_name, openapi_upstream  # noqa: F401


@pytest.fixture(autouse=True)
async def _fresh_http_upstream_client() -> AsyncGenerator[None, None]:
    """See test_extensions_dispatch.py's identical fixture: `http_upstream._client` is a
    module-level singleton bound to whichever event loop first uses it; recreate it per
    test so pytest-asyncio's per-test loop doesn't hit "Event loop is closed"."""
    previous = http_upstream._client
    http_upstream._client = httpx.AsyncClient()
    yield
    await http_upstream._client.aclose()
    http_upstream._client = previous


def _declared_body_registration(name: str, base_url: str) -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "declared-body test",
        "upstreams": {"svc": {"type": "http", "base_url": base_url}},
        "routes": [
            {
                "upstream": "svc",
                "path": f"/{name}/echo",
                "method": "POST",
                "upstream_path": "/echo",
                "visibility": "public",
                "body": {
                    "type": "object",
                    "required": ["a", "b"],
                    "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
                },
                "example": {"a": "x", "b": 1},
            }
        ],
    }


class TestRouteMetadata:
    async def test_openapi_entry_has_summary_and_extension_tag_with_no_manifest_tags(self, client):
        name = _unique_name("meta_ext")
        registration = {
            "schema_version": 1,
            "name": name,
            "description": "metadata test",
            "upstreams": {"svc": {"type": "http", "base_url": "http://localhost:9000"}},
            "routes": [
                {"upstream": "svc", "path": f"/{name}/ping", "method": "GET", "upstream_path": "/ping"},
            ],
        }
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        openapi = (await client.get("/openapi.json")).json()
        operation = openapi["paths"][f"/{name}/ping"]["get"]
        assert operation["summary"]
        assert f"Extension: {name}" in operation["tags"]

    async def test_two_routes_never_produce_the_same_operation_id(self, client):
        name = _unique_name("opid_ext")
        registration = {
            "schema_version": 1,
            "name": name,
            "description": "operation_id test",
            "upstreams": {"svc": {"type": "http", "base_url": "http://localhost:9000"}},
            "routes": [
                {"upstream": "svc", "path": f"/{name}/one", "method": "GET", "upstream_path": "/one"},
                {"upstream": "svc", "path": f"/{name}/two", "method": "GET", "upstream_path": "/two"},
            ],
        }
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        openapi = (await client.get("/openapi.json")).json()
        op_one = openapi["paths"][f"/{name}/one"]["get"]["operationId"]
        op_two = openapi["paths"][f"/{name}/two"]["get"]["operationId"]
        assert op_one != op_two

    async def test_declared_body_route_documents_request_body_schema_and_example(self, client, mock_http_upstream):
        name = _unique_name("bodydoc_ext")
        registration = _declared_body_registration(name, mock_http_upstream)
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        openapi = (await client.get("/openapi.json")).json()
        operation = openapi["paths"][f"/{name}/echo"]["post"]
        request_body_media = operation["requestBody"]["content"]["application/json"]
        assert request_body_media["schema"] == registration["routes"][0]["body"]
        assert request_body_media["example"] == registration["routes"][0]["example"]

    async def test_route_with_no_declared_body_has_no_request_body_in_openapi(self, client):
        name = _unique_name("nobody_ext")
        registration = {
            "schema_version": 1,
            "name": name,
            "description": "no-body metadata test",
            "upstreams": {"svc": {"type": "http", "base_url": "http://localhost:9000"}},
            "routes": [
                {"upstream": "svc", "path": f"/{name}/ping", "method": "GET", "upstream_path": "/ping"},
            ],
        }
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        openapi = (await client.get("/openapi.json")).json()
        operation = openapi["paths"][f"/{name}/ping"]["get"]
        assert "requestBody" not in operation


class TestDeclaredBodyValidation:
    async def test_malformed_example_is_rejected_at_registration(self, client, mock_http_upstream):
        name = _unique_name("bad_example_ext")
        registration = _declared_body_registration(name, mock_http_upstream)
        # 'b' must be an integer per the schema - this example violates its own route's body schema.
        registration["routes"][0]["example"] = {"a": "x", "b": "not-an-integer"}

        response = await client.post("/extensions", json=registration)

        assert response.status_code == 422
        # never registered silently
        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_valid_request_body_passes_through_to_upstream(self, client, mock_http_upstream):
        name = _unique_name("good_body_ext")
        registration = _declared_body_registration(name, mock_http_upstream)
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.post(f"/{name}/echo", json={"a": "x", "b": 1})

        assert response.status_code == 201
        assert response.json() == {"received": {"a": "x", "b": 1}}

    async def test_invalid_request_body_is_rejected_with_every_failing_field(self, client, mock_http_upstream):
        name = _unique_name("invalid_body_ext")
        registration = _declared_body_registration(name, mock_http_upstream)
        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.post(f"/{name}/echo", json={"b": "not-an-integer"})

        assert response.status_code == 422
        # This repo's global HTTPException handler (main.py) flattens every `detail`
        # into a `{"message": str(detail)}` string app-wide (not extension-specific,
        # and also true of this app's own native FastAPI Body(...) validation errors -
        # see RequestValidationError's handler) - so a caller can't distinguish this
        # from a native Body(...) parameter based on response shape alone.
        message = response.json()["message"]
        # missing 'a' (required) AND 'b' has the wrong type - both reported, not just the first
        assert "missing required propert" in message
        assert "must be of type integer" in message


class TestBodyRefRevalidationOnRestart:
    async def test_unreachable_ref_flips_to_upstream_declared_after_upstream_becomes_reachable(
        self, client, db_session, mock_http_upstream
    ):
        name = _unique_name("bodyref_ext")
        registration = {
            "schema_version": 1,
            "name": name,
            "description": "body_ref test",
            "upstreams": {"svc": {"type": "http", "base_url": "http://127.0.0.1:1"}},  # unreachable at registration
            "routes": [
                {
                    "upstream": "svc",
                    "path": f"/{name}/echo",
                    "method": "POST",
                    "upstream_path": "/echo",
                    "visibility": "public",
                    "body_ref": True,
                }
            ],
        }
        register_response = await client.post("/extensions", json=registration)
        assert register_response.status_code == 201
        assert register_response.json()["routes"][0]["validation_mode"] == "unreachable_ref"

        # Point the upstream at a now-reachable server (simulates the upstream coming
        # back up between two process restarts) without a manual PUT.
        replacement = dict(registration)
        replacement["upstreams"] = {"svc": {"type": "http", "base_url": mock_http_upstream}}
        replace_response = await client.put(f"/extensions/{name}", json=replacement)
        assert replace_response.status_code == 200
        # PUT replace itself already re-resolves body_ref, but /echo's mock server has no
        # /openapi.json (it's a plain http.server, not a FastAPI app) - still unreachable_ref.
        assert replace_response.json()["routes"][0]["validation_mode"] == "unreachable_ref"

        # This is the actual scenario under test: a startup-time refresh (extensions.setup.
        # hydrate_all_routes's call to registry.refresh_ref_schemas) picking up a schema
        # that a plain reachability check can't - simulate it by making the fetch succeed.
        # The startup refresh only covers enabled extensions.
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        async def _fake_fetch_openapi_document(base_url):
            body = {"content": {"application/json": {"schema": {"type": "object"}}}}
            return {"paths": {"/echo": {"post": {"requestBody": body}}}}

        original_fetch = http_upstream.fetch_openapi_document
        http_upstream.fetch_openapi_document = _fake_fetch_openapi_document
        try:
            extension_repo = get_repository(ExtensionRepository)(db_session)
            await registry.refresh_ref_schemas(extension_repo)
        finally:
            http_upstream.fetch_openapi_document = original_fetch

        get_response = await client.get(f"/extensions/{name}")
        assert get_response.status_code == 200
        assert get_response.json()["routes"][0]["validation_mode"] == "upstream_declared"

        await client.delete(f"/extensions/{name}")


def _items_openapi_document(item_component: str) -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/items": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ItemList"}}}},
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "ItemList": {"type": "array", "items": {"$ref": f"#/components/schemas/{item_component}"}},
                "ItemV1": {"type": "object", "properties": {"id": {"type": "string"}}},
                "ItemV2": {"type": "object", "properties": {"id": {"type": "integer"}}},
            }
        },
    }


def _response_ref_registration(name: str, base_url: str) -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "response_ref test",
        "upstreams": {"svc": {"type": "http", "base_url": base_url}},
        "routes": [
            {
                "upstream": "svc",
                "path": f"/{name}/items",
                "method": "GET",
                "upstream_path": "/items",
                "visibility": "public",
                "response_ref": True,
            }
        ],
    }


def _assert_refs_resolve(node, components: dict) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                assert value.startswith("#/components/schemas/")
                assert value.rsplit("/", 1)[-1] in components, value
            else:
                _assert_refs_resolve(value, components)
    elif isinstance(node, list):
        for item in node:
            _assert_refs_resolve(item, components)


class TestResponseRefDocumentation:
    async def test_response_ref_publishes_upstream_response_schema_with_resolvable_components(
        self, client, openapi_upstream
    ):
        name = _unique_name("responseref_ext")
        registration = _response_ref_registration(name, openapi_upstream.base_url)
        openapi_upstream.document = _items_openapi_document("ItemV1")

        register = await client.post("/extensions", json=registration)
        assert register.status_code == 201
        assert register.json()["routes"][0]["response_ref"] is True
        assert "response_schema" not in register.json()["routes"][0]
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        openapi = (await client.get("/openapi.json")).json()
        schema = openapi["paths"][f"/{name}/items"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        components = openapi["components"]["schemas"]
        _assert_refs_resolve(schema, components)
        assert schema["$ref"].rsplit("/", 1)[-1] in components
        item_refs = [key for key in components if key.endswith("_ItemV1")]
        assert item_refs and components[item_refs[0]]["properties"] == {"id": {"type": "string"}}
        await client.delete(f"/extensions/{name}")

    async def test_enable_refreshes_response_schema_and_disable_removes_its_documentation(
        self, client, openapi_upstream
    ):
        name = _unique_name("responseref_refresh_ext")
        openapi_upstream.document = _items_openapi_document("ItemV1")
        await client.post("/extensions", json=_response_ref_registration(name, openapi_upstream.base_url))
        await client.post(f"/extensions/{name}/enable")

        openapi_upstream.document = _items_openapi_document("ItemV2")
        await client.post(f"/extensions/{name}/disable")
        await client.post(f"/extensions/{name}/enable")

        openapi = (await client.get("/openapi.json")).json()
        components = openapi["components"]["schemas"]
        assert [key for key in components if key.endswith("_ItemV2")]
        assert not [key for key in components if key.endswith("_ItemV1")]
        _assert_refs_resolve(openapi["paths"][f"/{name}/items"]["get"], components)

        await client.post(f"/extensions/{name}/disable")
        openapi = (await client.get("/openapi.json")).json()
        assert f"/{name}/items" not in openapi["paths"]
        await client.delete(f"/extensions/{name}")
