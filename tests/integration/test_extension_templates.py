"""Protocol-neutral conformance tests for provider-declared template routes.

The providers below are test-only FastAPI/Pydantic contracts served by ephemeral
loopback HTTP servers. Management, ABAC, route dispatch, reference resolution and
OpenAPI publication remain the real application paths; no protocol catalog or cloud
service is involved.
"""
from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Query
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from jsonschema import Draft202012Validator

import auth
from authorization.abac_permission_check import ABACPermissionCheck
from db.repos.extension import ExtensionRepository
from db.repos.role import RoleRepository
from db.session import get_repository, get_db
from extensions import body_validation, setup as extensions_setup
from extensions.setup import internal_app
from extensions.upstreams import http as http_upstream
from main import app
from routers.module_config.registry import module_schema_registry
from tests.integration.test_abac_authorization import AbacFixtures
from tests.integration.test_extensions_management_routes import _unique_name


class ReadOnlyPlaceholder(BaseModel):
    name: str
    token: str


class ReadOnlyMetadata(BaseModel):
    revision: str
    source: str


class ReadOnlyTemplate(BaseModel):
    name: str
    title: str
    metadata: ReadOnlyMetadata
    placeholders: list[ReadOnlyPlaceholder]


class CrudSettings(BaseModel):
    retries: int
    enabled: bool


class CrudConfig(BaseModel):
    settings: CrudSettings


class CrudWriteRequest(BaseModel):
    template_name: str
    config: CrudConfig


class CrudPlaceholder(BaseModel):
    key: str
    description: str


class CrudTemplate(BaseModel):
    name: str
    config: CrudConfig
    placeholders: list[CrudPlaceholder]


class CrudDeleteResult(BaseModel):
    deleted: str


@dataclass
class _ProviderState:
    name: str
    url: str = ""
    document: dict = field(default_factory=dict)
    items: list[str] = field(default_factory=list)
    calls: list[tuple[str, str, str, Any]] = field(default_factory=list)
    fail_source: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, method: str, path: str, query: str = "", body: Any = None) -> None:
        with self.lock:
            self.calls.append((method, path, query, body))

    def call_count(self, path_suffix: str | None = None) -> int:
        with self.lock:
            if path_suffix is None:
                return len(self.calls)
            return sum(path.endswith(path_suffix) for _, path, _, _ in self.calls)


def _provider_openapi(name: str, crud: bool) -> dict:
    provider = FastAPI(title=f"Neutral fixture: {name}")

    @provider.get(f"/{name}/template/list", response_model=list[str], summary="List templates")
    async def list_templates():
        return []

    template_model = CrudTemplate if crud else ReadOnlyTemplate

    @provider.get(f"/{name}/template", response_model=template_model, summary="Read a template")
    async def get_template(template_name: Annotated[str, Query(...)]):
        return {}

    if crud:
        @provider.post(
            f"/{name}/template", response_model=CrudTemplate, status_code=201, summary="Create a template"
        )
        async def create_template(request: CrudWriteRequest):
            return {}

        @provider.delete(
            f"/{name}/template", response_model=CrudDeleteResult, summary="Delete a template"
        )
        async def delete_template(template_name: Annotated[str, Query(...)]):
            return {}

    return provider.openapi()


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        chunks = []
        while True:
            size = int(handler.rfile.readline().strip(), 16)
            if size == 0:
                handler.rfile.readline()
                break
            chunks.append(handler.rfile.read(size))
            handler.rfile.read(2)
        return b"".join(chunks)
    length = int(handler.headers.get("Content-Length", 0))
    return handler.rfile.read(length) if length else b""


@pytest.fixture
def local_provider_factory():
    """Start test-only OpenAPI/catalog servers and always close their loopback sockets."""
    servers: list[tuple[ThreadingHTTPServer, threading.Thread]] = []

    def start(name: str, *, crud: bool = False, items: list[str] | None = None) -> _ProviderState:
        state = _ProviderState(name=name, items=list(items or ["neutral-basic", "pump-assembly-v2"]))
        state.document = _provider_openapi(name, crud)

        class ProviderHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def respond(self, status: int, payload: Any) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                if parsed.path == "/openapi.json":
                    state.record("GET", parsed.path)
                    self.respond(200, state.document)
                    return
                state.record("GET", parsed.path, parsed.query)
                list_path = f"/{name}/template/list"
                template_path = f"/{name}/template"
                if parsed.path == list_path:
                    if state.fail_source:
                        self.respond(503, {"detail": "catalog source unavailable"})
                    else:
                        self.respond(200, list(state.items))
                elif parsed.path == template_path:
                    template_name = query.get("template_name", [""])[-1]
                    if template_name == "missing":
                        self.respond(404, {"detail": "template not found"})
                    elif template_name == "invalid":
                        self.respond(422, {"detail": "template name is invalid"})
                    elif crud:
                        self.respond(
                            200,
                            {
                                "name": template_name,
                                "config": {"settings": {"retries": 2, "enabled": True}},
                                "placeholders": [{"key": "unit_id", "description": "Unit identifier"}],
                            },
                        )
                    else:
                        self.respond(
                            200,
                            {
                                "name": template_name,
                                "title": "Reusable neutral template",
                                "metadata": {"revision": "r7", "source": "provider catalog"},
                                "placeholders": [{"name": "controller", "token": "{{controller_name}}"}],
                            },
                        )
                elif parsed.path.endswith("/probe"):
                    self.respond(200, {"probe": parsed.path})
                else:
                    self.respond(404, {"detail": "not found"})

            def do_POST(self):
                parsed = urlsplit(self.path)
                raw = _read_body(self)
                try:
                    payload = json.loads(raw) if raw else None
                except ValueError:
                    payload = None
                state.record("POST", parsed.path, parsed.query, payload)
                if parsed.path == f"/{name}/template" and crud:
                    try:
                        CrudWriteRequest.model_validate(payload)
                    except ValidationError as exc:
                        self.respond(422, {"detail": exc.errors()})
                        return
                    template_name = payload["template_name"]
                    if template_name not in state.items:
                        state.items.append(template_name)
                    # Deliberately unlike CrudTemplate: response_ref documents, but does
                    # not validate, materialize or rewrite the streamed provider response.
                    self.respond(
                        201,
                        {"stored": True, "provider_note": "relayed unchanged", "received": payload},
                    )
                elif parsed.path.endswith("/probe"):
                    self.respond(201, {"probe": parsed.path, "received": payload})
                else:
                    self.respond(404, {"detail": "not found"})

            def do_DELETE(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                state.record("DELETE", parsed.path, parsed.query)
                if parsed.path == f"/{name}/template" and crud:
                    template_name = query.get("template_name", [""])[-1]
                    if template_name not in state.items:
                        self.respond(404, {"detail": "template not found"})
                        return
                    state.items.remove(template_name)
                    self.respond(200, {"deleted": template_name})
                else:
                    self.respond(404, {"detail": "not found"})

        server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        state.url = f"http://127.0.0.1:{server.server_port}"
        return state

    try:
        yield start
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.fixture(autouse=True)
async def _fresh_http_upstream_client():
    """Keep the module-level HTTP client bound to this function-scoped event loop."""
    previous = http_upstream._client
    http_upstream._client = httpx.AsyncClient()
    try:
        yield
    finally:
        await http_upstream._client.aclose()
        http_upstream._client = previous


@pytest.fixture
def hydrate(db_session: AsyncSession, monkeypatch):
    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(extensions_setup, "AsyncSessionLocal", session)
    return extensions_setup.hydrate_all_routes


@pytest.fixture
async def owned_extensions(client, fake_jwt_user):
    names: list[str] = []

    def own(name: str) -> str:
        names.append(name)
        return name

    try:
        yield own
    finally:
        fake_jwt_user.clear()
        fake_jwt_user.update(
            oid="test-admin-oid", sub="test-admin-oid", preferred_username="admin@test.com",
            name="Test Admin", roles=["user.admin"],
        )
        cleanup_error = None
        for name in reversed(names):
            try:
                await client.delete(f"/extensions/{name}")
            except Exception as exc:
                cleanup_error = cleanup_error or exc
            finally:
                # The database outer transaction rolls back at test teardown; regardless
                # of management cleanup, the app must not retain test-owned routes.
                extensions_setup._unmount_extension(name)
                extensions_setup._extension_locks.pop(name, None)
        if cleanup_error is not None:
            raise cleanup_error


@pytest.fixture(autouse=True)
def _restore_extension_runtime_state():
    public_openapi = app.openapi_schema
    internal_openapi = internal_app.openapi_schema
    validators = body_validation._validator_cache.copy()
    active_schemas = module_schema_registry._active.copy()
    module_claims = module_schema_registry._claims.copy()
    unavailable_modules = module_schema_registry._unavailable.copy()
    try:
        yield
    finally:
        app.openapi_schema = public_openapi
        internal_app.openapi_schema = internal_openapi
        body_validation._validator_cache.clear()
        body_validation._validator_cache.update(validators)
        module_schema_registry._active = active_schemas
        module_schema_registry._claims = module_claims
        module_schema_registry._unavailable = unavailable_modules


def _action(name: str, suffix: str) -> str:
    return f"{name}.templates.{suffix}"


def _template_manifest(name: str, provider: _ProviderState, *, crud: bool, offline: bool = False) -> dict:
    list_action = _action(name, "list")
    read_action = _action(name, "read")
    actions = [
        {"name": list_action, "description": "List provider templates"},
        {"name": read_action, "description": "Read provider template content"},
    ]
    routes = [
        {
            "upstream": "svc",
            "path": f"/{name}/template/list",
            "upstream_path": f"/{name}/template/list",
            "method": "GET",
            "summary": "List neutral module templates",
            "response_ref": True,
            "required_action": list_action,
        },
        {
            "upstream": "svc",
            "path": f"/{name}/template",
            "upstream_path": f"/{name}/template",
            "method": "GET",
            "summary": "Read neutral module template document",
            "response_ref": True,
            "required_action": read_action,
            "query_params": [
                {"name": "template_name", "type": "string", "required": True, "description": "Provider catalog key"}
            ],
        },
    ]
    upstreams = {"svc": {"type": "http", "base_url": provider.url}}
    if crud:
        actions.extend(
            [
                {"name": _action(name, "write"), "description": "Create provider template"},
                {"name": _action(name, "delete"), "description": "Delete provider template"},
            ]
        )
        routes.extend(
            [
                {
                    "upstream": "svc",
                    "path": f"/{name}/template",
                    "upstream_path": f"/{name}/template",
                    "method": "POST",
                    "status_code": 201,
                    "summary": "Create neutral module template",
                    "body_ref": True,
                    "response_ref": True,
                    "required_action": _action(name, "write"),
                    "query_params": [
                        {"name": "template_name", "type": "string", "required": True, "description": "Provider catalog key"}
                    ],
                },
                {
                    "upstream": "svc",
                    "path": f"/{name}/template",
                    "upstream_path": f"/{name}/template",
                    "method": "DELETE",
                    "summary": "Delete neutral module template",
                    "response_ref": True,
                    "required_action": _action(name, "delete"),
                    "query_params": [
                        {"name": "template_name", "type": "string", "required": True, "description": "Provider catalog key"}
                    ],
                },
            ]
        )
    if offline:
        upstreams["offline"] = {"type": "http", "base_url": "http://127.0.0.1:1"}
        routes.append(
            {
                "upstream": "offline",
                "path": f"/{name}/offline-probe",
                "upstream_path": "/probe",
                "method": "GET",
                "summary": "Test core upstream failure mapping",
            }
        )
    return {
        "schema_version": 1,
        "name": name,
        "description": "Protocol-neutral provider-declared template contract fixture",
        "upstreams": upstreams,
        "actions": actions,
        "routes": routes,
    }


def _collect_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "$ref":
                refs.add(child)
            else:
                refs.update(_collect_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_collect_refs(child))
    return refs


def _transitive_component_refs(value: Any, components: dict) -> set[str]:
    refs = set()
    pending = list(_collect_refs(value))
    while pending:
        ref = pending.pop()
        if ref in refs:
            continue
        assert ref.startswith("#/components/schemas/")
        name = ref.rsplit("/", 1)[1]
        assert name in components, ref
        refs.add(ref)
        pending.extend(_collect_refs(components[name]))
    return refs


def _template_route(app_obj, name: str, path: str, method: str):
    return next(
        route
        for route in app_obj.router.routes
        if getattr(route, "extension_name", None) == name
        and route.path == path
        and method.upper() in (getattr(route, "methods", None) or set())
    )


def _required_action(route) -> str:
    calls = [dependency.call for dependency in route.dependant.dependencies]
    action = next(call for call in calls if isinstance(call, ABACPermissionCheck))
    assert action.device_path is None
    return action.permission


async def _register_and_enable(client, owned, name: str, provider: _ProviderState, *, crud: bool, offline=False):
    manifest = _template_manifest(name, provider, crud=crud, offline=offline)
    schema = (await client.get("/extensions/schema")).json()
    Draft202012Validator(schema).validate(manifest)
    owned(name)
    registered = await client.post("/extensions", json=manifest)
    assert registered.status_code == 201, registered.text
    enabled = await client.post(f"/extensions/{name}/enable")
    assert enabled.status_code == 200, enabled.text
    return manifest


async def test_neutral_template_manifests_dispatch_openapi_and_lifecycle(
    client, db_session, hydrate, local_provider_factory, owned_extensions
):
    readonly_name = _unique_name("neutral_ro")
    crud_name = _unique_name("neutral_crud")
    readonly = local_provider_factory(readonly_name, items=["pump-basic", "sensor-assembly"])
    crud = local_provider_factory(crud_name, crud=True, items=["robot-cell"])
    readonly_manifest = await _register_and_enable(
        client, owned_extensions, readonly_name, readonly, crud=False, offline=True
    )
    crud_manifest = await _register_and_enable(client, owned_extensions, crud_name, crud, crud=True)

    # Explicitly exercise the startup entrypoint; ASGITransport itself skips lifespan.
    await hydrate()
    readonly_list = await client.get(f"/{readonly_name}/template/list")
    crud_list_response = await client.get(f"/{crud_name}/template/list")
    assert readonly_list.status_code == crud_list_response.status_code == 200
    assert type(readonly_list.json()) is list and all(isinstance(item, str) for item in readonly_list.json())
    assert readonly_list.json() == ["pump-basic", "sensor-assembly"]
    assert crud_list_response.json() == ["robot-cell"]
    assert readonly.calls[-1][1] == f"/{readonly_name}/template/list"
    assert crud.calls[-1][1] == f"/{crud_name}/template/list"

    content_path = f"/{readonly_name}/template"
    missing_query = await client.get(content_path)
    assert missing_query.status_code == 400
    special_name = "Δ pump / rev?x=1&suffix=.json"
    content = await client.get(content_path, params={"template_name": special_name})
    assert content.status_code == 200
    assert content.json() == {
        "name": special_name,
        "title": "Reusable neutral template",
        "metadata": {"revision": "r7", "source": "provider catalog"},
        "placeholders": [{"name": "controller", "token": "{{controller_name}}"}],
    }
    forwarded_path, forwarded_query, forwarded_body = readonly.calls[-1][1:]
    assert forwarded_path == f"/{readonly_name}/template"
    assert parse_qs(forwarded_query, keep_blank_values=True) == {"template_name": [special_name]}
    assert "%CE%94" in forwarded_query and "%2F" in forwarded_query and "%26suffix%3D" in forwarded_query
    assert forwarded_body is None

    assert (await client.get(content_path, params={"template_name": "missing"})).status_code == 404
    assert (await client.get(content_path, params={"template_name": "invalid"})).status_code == 422
    readonly.items.clear()
    assert (await client.get(f"/{readonly_name}/template/list")).json() == []
    readonly.fail_source = True
    source_failure = await client.get(f"/{readonly_name}/template/list")
    assert source_failure.status_code == 503
    assert source_failure.json() == {"detail": "catalog source unavailable"}
    assert (await client.get(f"/{readonly_name}/offline-probe")).status_code == 502

    crud_path = f"/{crud_name}/template"
    crud_special_name = "servo Δ / version 4?variant=a&suffix=.json"
    crud_read = await client.get(crud_path, params={"template_name": crud_special_name})
    assert crud_read.status_code == 200
    assert crud_read.json()["name"] == crud_special_name
    assert crud_read.json()["placeholders"] == [{"key": "unit_id", "description": "Unit identifier"}]

    request_body = {
        "template_name": "robot-cell-v3",
        "config": {"settings": {"retries": 3, "enabled": True}},
    }
    created = await client.post(crud_path, params={"template_name": request_body["template_name"]}, json=request_body)
    assert created.status_code == 201
    assert created.json() == {
        "stored": True,
        "provider_note": "relayed unchanged",
        "received": request_body,
    }
    assert crud.calls[-1][0:2] == ("POST", f"/{crud_name}/template")
    assert crud.calls[-1][3] == request_body
    invalid_create = await client.post(
        crud_path,
        params={"template_name": "invalid-body"},
        json={"template_name": "invalid-body", "config": {"settings": {"retries": "many", "enabled": True}}},
    )
    assert invalid_create.status_code == 422
    assert (await client.delete(crud_path)).status_code == 400
    deleted = await client.delete(crud_path, params={"template_name": request_body["template_name"]})
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": request_body["template_name"]}
    assert crud.calls[-1][0:2] == ("DELETE", f"/{crud_name}/template")

    # Every declared template operation is visible; absent read-only writes remain absent.
    openapi = (await client.get("/openapi.json")).json()
    ro_list = openapi["paths"][f"/{readonly_name}/template/list"]
    ro_content = openapi["paths"][content_path]
    assert set(ro_list) == {"get"}
    assert set(ro_content) == {"get"}
    crud_list = openapi["paths"][f"/{crud_name}/template/list"]
    crud_template = openapi["paths"][crud_path]
    assert set(crud_list) == {"get"}
    assert set(crud_template) == {"get", "post", "delete"}
    assert crud_template["get"]["parameters"][0]["name"] == "template_name"
    assert crud_template["get"]["parameters"][0]["in"] == "query"
    assert crud_template["get"]["parameters"][0]["required"] is True
    assert crud_template["post"]["parameters"][0]["required"] is True
    assert crud_template["delete"]["parameters"][0]["required"] is True
    assert crud_template["post"]["requestBody"]["required"] is True
    list_schema = crud_list["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert list_schema["type"] == "array" and list_schema["items"]["type"] == "string"
    assert crud_template["post"]["summary"] == "Create neutral module template"
    assert crud_template["get"]["summary"] == "Read neutral module template document"
    assert crud_template["delete"]["summary"] == "Delete neutral module template"

    request_schema = crud_template["post"]["requestBody"]["content"]["application/json"]["schema"]
    response_schema = crud_template["post"]["responses"]["201"]["content"]["application/json"]["schema"]
    components = openapi["components"]["schemas"]
    request_refs = _transitive_component_refs(request_schema, components)
    response_refs = _transitive_component_refs(response_schema, components)
    assert len(request_refs) >= 2 and len(response_refs) >= 2
    request_root = next(iter(_collect_refs(request_schema))).rsplit("/", 1)[1]
    response_root = next(iter(_collect_refs(response_schema))).rsplit("/", 1)[1]
    assert {"template_name", "config"} <= set(components[request_root]["properties"])
    assert "config" in components[response_root]["properties"]
    assert "placeholders" in components[response_root]["properties"]
    assert request_refs.isdisjoint(response_refs)
    for ref in request_refs | response_refs:
        assert ref.startswith("#/components/schemas/ext_")
        assert ref.rsplit("/", 1)[1] in components
    ro_refs = _transitive_component_refs(ro_content["get"]["responses"]["200"], components)
    read_refs = _transitive_component_refs(crud_template["get"]["responses"]["200"], components)
    route_refs = [request_refs, response_refs, ro_refs, read_refs]
    for index, refs in enumerate(route_refs):
        assert all(refs.isdisjoint(other) for other in route_refs[index + 1 :])
    all_refs = set().union(*route_refs)
    assert len({ref.rsplit("/", 1)[1] for ref in all_refs}) == len(all_refs)
    assert _required_action(_template_route(app, readonly_name, f"/{readonly_name}/template/list", "GET")) == _action(readonly_name, "list")
    assert _required_action(_template_route(app, readonly_name, content_path, "GET")) == _action(readonly_name, "read")
    assert _required_action(_template_route(app, crud_name, f"/{crud_name}/template/list", "GET")) == _action(crud_name, "list")
    assert _required_action(_template_route(app, crud_name, crud_path, "GET")) == _action(crud_name, "read")
    assert _required_action(_template_route(app, crud_name, crud_path, "POST")) == _action(crud_name, "write")
    assert _required_action(_template_route(app, crud_name, crud_path, "DELETE")) == _action(crud_name, "delete")
    assert crud_manifest["routes"][2]["body_ref"] is True
    assert crud_manifest["routes"][2]["response_ref"] is True
    assert readonly_manifest["routes"][0]["response_ref"] is True

    old_crud_path = crud_path
    old_component_names = {
        ref.rsplit("/", 1)[1] for ref in request_refs | response_refs | read_refs
    }
    original_routes = await get_repository(ExtensionRepository)(db_session).list_routes(crud_name)
    assert any(row.get("validation_mode") == "upstream_declared" for row in original_routes)
    replacement = {
        "schema_version": 1,
        "name": crud_name,
        "description": "replacement with only the list operation",
        "upstreams": {"svc": {"type": "http", "base_url": crud.url}},
        "actions": [{"name": _action(crud_name, "list"), "description": "List provider templates"}],
        "routes": [
            {
                "upstream": "svc",
                "path": f"/{crud_name}/template-list-v2",
                "upstream_path": f"/{crud_name}/template/list",
                "method": "GET",
                "summary": "Replacement list",
                "response_ref": True,
                "required_action": _action(crud_name, "list"),
            }
        ],
    }
    replaced = await client.put(f"/extensions/{crud_name}", json=replacement)
    assert replaced.status_code == 200, replaced.text
    after_replace = (await client.get("/openapi.json")).json()
    assert old_crud_path not in after_replace["paths"]
    assert f"/{crud_name}/template/list" not in after_replace["paths"]
    assert not (old_component_names & set(after_replace["components"]["schemas"]))
    assert (await client.get(f"/{crud_name}/template-list-v2")).status_code == 200
    assert not any(
        getattr(route, "extension_name", None) == crud_name and route.path == old_crud_path
        for route in app.router.routes
    )

    readonly_component_names = {ref.rsplit("/", 1)[1] for ref in ro_refs}
    assert (await client.post(f"/extensions/{readonly_name}/disable")).status_code == 200
    disabled_openapi = (await client.get("/openapi.json")).json()
    assert f"/{readonly_name}/template/list" not in disabled_openapi["paths"]
    assert not (readonly_component_names & set(disabled_openapi["components"]["schemas"]))
    assert (await client.get(f"/{readonly_name}/template/list")).status_code == 404
    assert (await client.post(f"/extensions/{readonly_name}/enable")).status_code == 200
    assert (await client.delete(f"/extensions/{readonly_name}")).status_code == 204
    after_readonly_delete = (await client.get("/openapi.json")).json()
    assert f"/{readonly_name}/template/list" not in after_readonly_delete["paths"]
    assert not (readonly_component_names & set(after_readonly_delete["components"]["schemas"]))

    replacement_refs = _transitive_component_refs(
        after_replace["paths"][f"/{crud_name}/template-list-v2"]["get"]["responses"]["200"],
        after_replace["components"]["schemas"],
    )
    replacement_component_names = {ref.rsplit("/", 1)[1] for ref in replacement_refs}
    assert (await client.post(f"/extensions/{crud_name}/disable")).status_code == 200
    after_crud_disable = (await client.get("/openapi.json")).json()
    assert f"/{crud_name}/template-list-v2" not in after_crud_disable["paths"]
    assert not (replacement_component_names & set(after_crud_disable["components"]["schemas"]))
    assert (await client.delete(f"/extensions/{crud_name}")).status_code == 204
    after_crud_delete = (await client.get("/openapi.json")).json()
    assert f"/{crud_name}/template-list-v2" not in after_crud_delete["paths"]
    assert not (replacement_component_names & set(after_crud_delete["components"]["schemas"]))
    assert not any(getattr(route, "extension_name", None) == crud_name for route in app.router.routes)


async def test_template_actions_require_explicit_grants_and_real_seeded_users(
    client, db_session, fake_jwt_user, local_provider_factory, owned_extensions, monkeypatch
):
    ro_name = _unique_name("abac_ro")
    crud_name = _unique_name("abac_crud")
    readonly = local_provider_factory(ro_name)
    crud = local_provider_factory(crud_name, crud=True, items=["robot-cell"])
    await _register_and_enable(client, owned_extensions, ro_name, readonly, crud=False)
    await _register_and_enable(client, owned_extensions, crud_name, crud, crud=True)

    actions = [
        _action(ro_name, "list"), _action(ro_name, "read"),
        _action(crud_name, "list"), _action(crud_name, "read"),
        _action(crud_name, "write"), _action(crud_name, "delete"),
    ]
    roles_before = await get_repository(RoleRepository)(db_session).list_roles()
    assert not any(set(actions) & set(role.get("actions") or []) for role in roles_before)

    users = {label: f"template-{uuid4().hex}" for label in ("list", "read", "write", "delete", "none")}
    roles = {
        "list-role": [_action(ro_name, "list"), _action(crud_name, "list")],
        "read-role": [_action(ro_name, "read"), _action(crud_name, "read")],
        "write-role": [_action(crud_name, "write")],
        "delete-role": [_action(crud_name, "delete")],
    }
    world = await AbacFixtures(db_session).setup(
        users=users,
        roles=roles,
        teams={
            "list-team": {"roles": ["list-role"], "users": ["list"]},
            "read-team": {"roles": ["read-role"], "users": ["read"]},
            "write-team": {"roles": ["write-role"], "users": ["write"]},
            "delete-team": {"roles": ["delete-role"], "users": ["delete"]},
            "no-grant-team": {"users": ["none"]},
        },
    )

    def caller(label: str) -> None:
        oid = world.users[label]
        fake_jwt_user.clear()
        fake_jwt_user.update(
            oid=oid,
            sub=oid,
            preferred_username=f"{label}@test.com",
            name=label,
            roles=[],
        )

    ro_list = f"/{ro_name}/template/list"
    ro_get = f"/{ro_name}/template"
    crud_list = f"/{crud_name}/template/list"
    crud_get = f"/{crud_name}/template"
    body = {"template_name": "writer-item", "config": {"settings": {"retries": 1, "enabled": True}}}

    caller("none")
    before = (readonly.call_count(), crud.call_count())
    denied_paths = [
        ("get", ro_list, None), ("get", ro_get, {"template_name": "pump-basic"}),
        ("get", crud_list, None), ("get", crud_get, {"template_name": "robot-cell"}),
        ("post", crud_get, {"template_name": body["template_name"]}),
        ("delete", crud_get, {"template_name": "robot-cell"}),
    ]
    for method, path, params in denied_paths:
        kwargs = {"params": params}
        if method == "post":
            kwargs["json"] = body
        response = await getattr(client, method)(path, **kwargs)
        assert response.status_code == 403
    assert (readonly.call_count(), crud.call_count()) == before

    caller("list")
    assert (await client.get(ro_list)).status_code == 200
    assert (await client.get(crud_list)).status_code == 200
    assert (await client.get(crud_get, params={"template_name": "robot-cell"})).status_code == 403
    caller("read")
    assert (await client.get(ro_get, params={"template_name": "pump-basic"})).status_code == 200
    assert (await client.get(crud_get, params={"template_name": "robot-cell"})).status_code == 200
    assert (await client.get(crud_list)).status_code == 403
    caller("write")
    assert (await client.post(crud_get, params={"template_name": body["template_name"]}, json=body)).status_code == 201
    assert (await client.delete(crud_get, params={"template_name": "robot-cell"})).status_code == 403
    assert (await client.get(crud_list)).status_code == 403
    caller("delete")
    assert (await client.delete(crud_get, params={"template_name": "robot-cell"})).status_code == 200
    assert (await client.post(crud_get, params={"template_name": body["template_name"]}, json=body)).status_code == 403
    assert (await client.get(crud_get, params={"template_name": body["template_name"]})).status_code == 403

    async def forbidden_oidc():
        raise AssertionError("Missing bearer credentials must be rejected before OIDC network access")

    monkeypatch.setattr(auth, "ensure_oidc_initialized", forbidden_oidc)
    previous = app.dependency_overrides.pop(auth.validate_jwt)
    calls = (readonly.call_count(), crud.call_count())
    try:
        unauthenticated = await client.get(ro_list)
        assert unauthenticated.status_code == 401
        assert unauthenticated.json() == {"message": "Not authenticated"}
        assert (readonly.call_count(), crud.call_count()) == calls
    finally:
        app.dependency_overrides[auth.validate_jwt] = previous


async def test_exact_active_route_claims_are_atomic_across_extensions_and_visibility(
    client, db_session, local_provider_factory, owned_extensions
):
    from extensions import body_validation as validators

    owner_name = _unique_name("claim_owner")
    disabled_name = _unique_name("claim_disabled")
    split_name = _unique_name("claim_split")
    replacement_name = _unique_name("claim_replacement")
    owner_service = local_provider_factory(owner_name)
    disabled_service = local_provider_factory(disabled_name)
    split_service = local_provider_factory(split_name)
    replacement_service = local_provider_factory(replacement_name)
    shared_path = "/neutral-contract/shared-probe"

    owner_actions = [
        {"name": f"{owner_name}.probe.read", "description": "Owner GET probe"},
        {"name": f"{owner_name}.probe.write", "description": "Owner POST probe"},
    ]
    owner_manifest = {
        "schema_version": 1,
        "name": owner_name,
        "description": "Active public route owner",
        "upstreams": {"svc": {"type": "http", "base_url": owner_service.url}},
        "actions": owner_actions,
        "routes": [
            {
                "upstream": "svc", "path": shared_path, "upstream_path": f"/{owner_name}/probe",
                "method": "GET", "required_action": f"{owner_name}.probe.read",
            },
            {
                "upstream": "svc", "path": f"/{owner_name}/owner-probe", "upstream_path": f"/{owner_name}/probe",
                "method": "POST", "required_action": f"{owner_name}.probe.write",
                "body": {"type": "object", "required": ["value"], "properties": {"value": {"type": "string"}}},
            },
        ],
    }
    disabled_manifest = {
        "schema_version": 1,
        "name": disabled_name,
        "description": "Disabled conflicting claimant",
        "upstreams": {"svc": {"type": "http", "base_url": disabled_service.url}},
        "actions": [
            {"name": f"{disabled_name}.probe.read", "description": "GET probe"},
            {"name": f"{disabled_name}.probe.write", "description": "POST probe"},
        ],
        "routes": [
            {"upstream": "svc", "path": shared_path, "upstream_path": f"/{disabled_name}/probe", "method": "GET", "required_action": f"{disabled_name}.probe.read"},
            {"upstream": "svc", "path": shared_path, "upstream_path": f"/{disabled_name}/probe", "method": "POST", "required_action": f"{disabled_name}.probe.write"},
        ],
    }
    owned_extensions(owner_name)
    owned_extensions(disabled_name)
    assert (await client.post("/extensions", json=owner_manifest)).status_code == 201
    assert (await client.post("/extensions", json=disabled_manifest)).status_code == 201
    assert (await client.post(f"/extensions/{owner_name}/enable")).status_code == 200
    # The disabled registration above has the same GET/path claim but owns no live route.
    assert (await client.get(shared_path)).json() == {"probe": f"/{owner_name}/probe"}

    owner_rows = await get_repository(ExtensionRepository)(db_session).list_routes(owner_name)
    cached_route_id = str(next(row["id"] for row in owner_rows if row["method"] == "POST"))
    assert (await client.post(f"/{owner_name}/owner-probe", json={"wrong": "shape"})).status_code == 422
    assert cached_route_id in validators._validator_cache

    # Self-replacement is legal, and unmounting the old route invalidates its validator.
    owner_replacement = dict(owner_manifest, description="Self replacement retains its claims")
    self_replace = await client.put(f"/extensions/{owner_name}", json=owner_replacement)
    assert self_replace.status_code == 200, self_replace.text
    assert cached_route_id not in validators._validator_cache
    new_owner_rows = await get_repository(ExtensionRepository)(db_session).list_routes(owner_name)
    new_cached_id = str(next(row["id"] for row in new_owner_rows if row["method"] == "POST"))
    assert (await client.post(f"/{owner_name}/owner-probe", json={"wrong": "shape"})).status_code == 422
    assert new_cached_id in validators._validator_cache

    before_conflict = (await client.get(f"/extensions/{disabled_name}")).json()
    conflict = await client.post(f"/extensions/{disabled_name}/enable")
    assert conflict.status_code == 409
    after_conflict = (await client.get(f"/extensions/{disabled_name}")).json()
    assert after_conflict["enabled"] is False
    assert after_conflict["description"] == before_conflict["description"]
    assert [
        (route["path"], route["method"], route["upstream_path"], route["required_action"])
        for route in after_conflict["routes"]
    ] == [
        (route["path"], route["method"], route["upstream_path"], route["required_action"])
        for route in before_conflict["routes"]
    ]
    assert not any(getattr(route, "extension_name", None) == disabled_name for route in app.router.routes)
    assert (await client.get(shared_path)).json() == {"probe": f"/{owner_name}/probe"}

    # Same path/different method and same path in public/internal routing namespaces are separate claims.
    split_manifest = {
        "schema_version": 1,
        "name": split_name,
        "description": "Different method and routing namespace claims",
        "upstreams": {"svc": {"type": "http", "base_url": split_service.url}},
        "actions": [{"name": f"{split_name}.probe.write", "description": "POST probe"}],
        "routes": [
            {"upstream": "svc", "path": shared_path, "upstream_path": f"/{split_name}/probe", "method": "POST", "required_action": f"{split_name}.probe.write"},
            {"upstream": "svc", "path": shared_path, "upstream_path": f"/{split_name}/probe", "method": "GET", "visibility": "internal"},
        ],
    }
    owned_extensions(split_name)
    assert (await client.post("/extensions", json=split_manifest)).status_code == 201
    assert (await client.post(f"/extensions/{split_name}/enable")).status_code == 200
    assert (await client.post(shared_path, json={"probe": True})).json() == {
        "probe": f"/{split_name}/probe", "received": {"probe": True}
    }

    async def override_get_db():
        yield db_session

    previous_internal_overrides = dict(internal_app.dependency_overrides)
    internal_app.dependency_overrides[get_db] = override_get_db
    internal_transport = httpx.ASGITransport(app=internal_app, raise_app_exceptions=True)
    try:
        key_response = await client.post(f"/extensions/{split_name}/internal-key/rotate")
        assert key_response.status_code == 200
        async with httpx.AsyncClient(transport=internal_transport, base_url="http://internal") as internal_client:
            internal = await internal_client.get(
                shared_path, headers={"X-Internal-Key": key_response.json()["internal_key"]}
            )
        assert internal.status_code == 200
        assert internal.json() == {"probe": f"/{split_name}/probe"}
    finally:
        internal_app.dependency_overrides.clear()
        internal_app.dependency_overrides.update(previous_internal_overrides)

    # A conflicting enabled replacement rejects before either the stored manifest or live route changes.
    before_split = (await client.get(f"/extensions/{split_name}")).json()
    conflicting_replacement = {
        **split_manifest,
        "description": "This replacement must be rejected atomically",
        "routes": [
            {"upstream": "svc", "path": shared_path, "upstream_path": f"/{split_name}/probe", "method": "GET", "required_action": f"{split_name}.probe.write"}
        ],
    }
    rejected = await client.put(f"/extensions/{split_name}", json=conflicting_replacement)
    assert rejected.status_code == 409
    after_split = (await client.get(f"/extensions/{split_name}")).json()
    assert after_split["enabled"] is True
    assert after_split["description"] == before_split["description"]
    assert [(r["path"], r["method"], r["visibility"]) for r in after_split["routes"]] == [
        (r["path"], r["method"], r["visibility"]) for r in before_split["routes"]
    ]
    assert (await client.post(shared_path, json={"probe": "still-active"})).status_code == 201
    assert (await client.get(shared_path)).json() == {"probe": f"/{owner_name}/probe"}

    # Disable releases A's exact public GET claim; the formerly disabled claimant can now enable.
    assert (await client.post(f"/extensions/{split_name}/disable")).status_code == 200
    assert (await client.post(f"/extensions/{owner_name}/disable")).status_code == 200
    assert new_cached_id not in validators._validator_cache
    assert (await client.post(f"/extensions/{disabled_name}/enable")).status_code == 200
    assert (await client.get(shared_path)).json() == {"probe": f"/{disabled_name}/probe"}
    assert (await client.delete(f"/extensions/{disabled_name}")).status_code == 204

    # Deregistration also releases the claim for a fresh extension.
    released_name = _unique_name("claim_released")
    released_service = local_provider_factory(released_name)
    released_manifest = {
        "schema_version": 1,
        "name": released_name,
        "description": "Uses a released public claim",
        "upstreams": {"svc": {"type": "http", "base_url": released_service.url}},
        "routes": [
            {"upstream": "svc", "path": shared_path, "upstream_path": f"/{released_name}/probe", "method": "GET"}
        ],
    }
    owned_extensions(released_name)
    assert (await client.post("/extensions", json=released_manifest)).status_code == 201
    assert (await client.post(f"/extensions/{released_name}/enable")).status_code == 200
    assert (await client.get(shared_path)).json() == {"probe": f"/{released_name}/probe"}
