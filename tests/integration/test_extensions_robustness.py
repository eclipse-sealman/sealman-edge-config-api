import asyncio
from collections.abc import AsyncGenerator, Generator
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from auth import validate_jwt
from db.models.action import Action
from db.models.extension import Extension, ExtensionAction, ExtensionRoute, ExtensionUpstream
from db.session import get_db
from extensions.upstreams import http as http_upstream
from main import app


@pytest.fixture
async def committed_client(
    postgres_container: str,
    apply_migrations: None,
) -> AsyncGenerator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]], None]:
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def override_validate_jwt():
        return {
            "oid": "robustness-admin-oid",
            "sub": "robustness-admin-oid",
            "preferred_username": "robustness-admin@test.com",
            "name": "Robustness Admin",
            "roles": ["user.admin"],
        }

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[validate_jwt] = override_validate_jwt
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, session_factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


class _RobustUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            while True:
                chunk_size = int(self.rfile.readline().strip(), 16)
                if chunk_size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(chunk_size))
                self.rfile.read(2)
            return b"".join(chunks)
        content_length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(content_length)

    def _write_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Vary", "Origin")
        self.send_header("Set-Cookie", "upstream-session=secret")
        self.send_header("X-Upstream-Secret", "must-not-leak")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path.startswith("/slow"):
            self.server.slow_request_started.set()
            time.sleep(1.25)
            self._write_json(200, {"slow": True})
            return
        if self.path.startswith("/missing"):
            self._write_json(418, {"detail": "deliberate upstream error"})
            return
        self._write_json(
            200,
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
                "x_internal_key": self.headers.get("X-Internal-Key"),
                "x_request_id": self.headers.get("X-Request-ID"),
                "x_unlisted": self.headers.get("X-Unlisted"),
                "x_forwarded_for": self.headers.get("X-Forwarded-For"),
                "x_forwarded_host": self.headers.get("X-Forwarded-Host"),
                "x_forwarded_proto": self.headers.get("X-Forwarded-Proto"),
            },
        )

    def do_POST(self) -> None:
        body = self._read_body()
        self._write_json(
            202,
            {
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "transfer_encoding": self.headers.get("Transfer-Encoding"),
            },
        )


@pytest.fixture
def robust_http_upstream() -> Generator[tuple[str, threading.Event], None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RobustUpstreamHandler)
    server.slow_request_started = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server.slow_request_started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(autouse=True)
async def fresh_proxy_client() -> AsyncGenerator[None, None]:
    previous = http_upstream._client
    http_upstream._client = httpx.AsyncClient()
    try:
        yield
    finally:
        await http_upstream._client.aclose()
        http_upstream._client = previous


def _robust_registration(name: str, base_url: str = "http://127.0.0.1:1") -> dict:
    action_name = f"{name}.write"
    return {
        "schema_version": 1,
        "name": name,
        "description": "Quotes: 'single' and \"double\"; newline:\nsecond line",
        "upstreams": {
            "primary-service": {
                "type": "http",
                "base_url": base_url,
                "health_path": "/health?deep=true",
                "version_field": "meta.release",
                "expected_version": "2026.09+build.7",
            }
        },
        "actions": [{"name": action_name, "description": "Write nested widgets"}],
        "routes": [
            {
                "upstream": "primary-service",
                "path": f"/{name}/widgets/{{widget_id}}",
                "method": "POST",
                "summary": "Create or replace a widget",
                "tags": ["widgets", "robustness"],
                "status_code": 201,
                "query_params": [
                    {
                        "name": "dry_run",
                        "type": "boolean",
                        "required": False,
                        "description": "Validate without saving",
                    }
                ],
                "body": {
                    "type": "object",
                    "required": ["label", "measurements"],
                    "properties": {
                        "label": {"type": "string", "minLength": 1},
                        "measurements": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 1,
                        },
                        "metadata": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "additionalProperties": False,
                },
                "example": {
                    "label": "pump-A",
                    "measurements": [1, 2.5],
                    "metadata": {"site": "north"},
                },
                "required_action": action_name,
                "upstream_path": "/widgets/{widget_id}",
                "visibility": "public",
            }
        ],
    }


async def test_funky_manifest_round_trips_through_normalized_database(committed_client):
    client, session_factory = committed_client
    name = f"robust_manifest_{uuid4().hex[:10]}"
    registration = _robust_registration(name)

    response = await client.post("/extensions", json=registration)
    assert response.status_code == 201, response.text

    async with session_factory() as session:
        extension = (
            await session.execute(select(Extension).where(Extension.name == name))
        ).scalar_one()
        upstream = (
            await session.execute(
                select(ExtensionUpstream).where(ExtensionUpstream.extension_id == extension.id)
            )
        ).scalar_one()
        route = (
            await session.execute(
                select(ExtensionRoute).where(ExtensionRoute.extension_id == extension.id)
            )
        ).scalar_one()
        action = (
            await session.execute(
                select(ExtensionAction).where(ExtensionAction.extension_id == extension.id)
            )
        ).scalar_one()

        assert extension.description == registration["description"]
        assert upstream.key == "primary-service"
        assert upstream.expected_version == "2026.09+build.7"
        assert route.path == registration["routes"][0]["path"]
        assert route.query_params == registration["routes"][0]["query_params"]
        assert route.body == registration["routes"][0]["body"]
        assert route.example == registration["routes"][0]["example"]
        assert route.validation_mode == "declared"
        assert action.action_name == registration["actions"][0]["name"]

    get_response = await client.get(f"/extensions/{name}")
    assert get_response.status_code == 200
    detail = get_response.json()
    assert detail["description"] == registration["description"]
    assert detail["routes"][0]["body"] == registration["routes"][0]["body"]
    assert detail["routes"][0]["query_params"] == registration["routes"][0]["query_params"]

    delete_response = await client.delete(f"/extensions/{name}")
    assert delete_response.status_code == 204
    assert (await client.get(f"/extensions/{name}")).status_code == 404
    async with session_factory() as session:
        assert await session.scalar(select(Extension).where(Extension.name == name)) is None
        assert await session.scalar(
            select(ExtensionAction).where(ExtensionAction.action_name == registration["actions"][0]["name"])
        ) is None
        assert await session.scalar(
            select(Action).where(Action.name == registration["actions"][0]["name"])
        ) is None


async def test_proxy_streaming_headers_errors_and_event_loop_responsiveness(
    committed_client,
    robust_http_upstream,
):
    client, _ = committed_client
    base_url, slow_request_started = robust_http_upstream
    name = f"robust_proxy_{uuid4().hex[:10]}"
    registration = {
        "schema_version": 1,
        "name": name,
        "upstreams": {"svc": {"type": "http", "base_url": base_url}},
        "routes": [
            {
                "upstream": "svc",
                "path": f"/{name}/echo/{{item}}",
                "method": "GET",
                "upstream_path": "/echo/{item}",
            },
            {
                "upstream": "svc",
                "path": f"/{name}/upload",
                "method": "POST",
                "upstream_path": "/upload",
            },
            {
                "upstream": "svc",
                "path": f"/{name}/slow",
                "method": "GET",
                "upstream_path": "/slow",
            },
            {
                "upstream": "svc",
                "path": f"/{name}/upstream-error",
                "method": "GET",
                "upstream_path": "/missing",
            },
        ],
    }

    assert (await client.post("/extensions", json=registration)).status_code == 201
    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

    echo_response = await client.get(
        f"/{name}/echo/value%20with%20spaces",
        params=[("filter", "alpha"), ("filter", "beta")],
        headers={
            "Authorization": "Bearer platform-secret",
            "Cookie": "platform-session=secret",
            "X-Internal-Key": "internal-secret",
            "X-Request-ID": "request-robust-123",
            "X-Unlisted": "must-not-pass",
            "X-Forwarded-For": "attacker.invalid",
            "X-Forwarded-Host": "attacker.invalid",
            "X-Forwarded-Proto": "gopher",
        },
    )
    assert echo_response.status_code == 200
    echoed = echo_response.json()
    assert echoed["path"] == "/echo/value%20with%20spaces?filter=alpha&filter=beta"
    assert echoed["authorization"] is None
    assert echoed["cookie"] is None
    assert echoed["x_internal_key"] is None
    assert echoed["x_unlisted"] is None
    assert echoed["x_request_id"] == "request-robust-123"
    assert echoed["x_forwarded_for"] != "attacker.invalid"
    assert echoed["x_forwarded_host"] == "test"
    assert echoed["x_forwarded_proto"] == "http"
    assert echo_response.headers.get_list("vary") == ["Accept-Encoding", "Origin"]
    for denied_header in ("connection", "date", "server", "set-cookie", "x-upstream-secret"):
        assert denied_header not in echo_response.headers

    payload = (b"0123456789abcdef" * 131_072) + b"tail"
    upload_response = await client.post(
        f"/{name}/upload",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload_response.status_code == 202
    assert upload_response.json()["size"] == len(payload)
    assert upload_response.json()["sha256"] == hashlib.sha256(payload).hexdigest()

    upstream_error = await client.get(f"/{name}/upstream-error")
    assert upstream_error.status_code == 418
    assert upstream_error.json() == {"detail": "deliberate upstream error"}

    slow_task = asyncio.create_task(client.get(f"/{name}/slow"))
    assert await asyncio.to_thread(slow_request_started.wait, 1.0)
    started_at = time.perf_counter()
    schema_response = await client.get("/extensions/schema")
    responsiveness_seconds = time.perf_counter() - started_at
    assert schema_response.status_code == 200
    assert responsiveness_seconds < 0.5
    assert (await slow_task).status_code == 200

    assert (await client.delete(f"/extensions/{name}")).status_code == 204
    assert (await client.get(f"/{name}/slow")).status_code == 404


async def test_adversarial_manifests_are_rejected_without_database_residue(committed_client):
    client, session_factory = committed_client

    def unknown_upstream(payload: dict) -> None:
        payload["routes"][0]["upstream"] = "missing"

    def body_and_body_ref(payload: dict) -> None:
        payload["routes"][0]["body_ref"] = True

    def malformed_json_schema(payload: dict) -> None:
        payload["routes"][0]["body"] = {"type": "not-a-json-schema-type"}
        payload["routes"][0].pop("example")

    def invalid_example(payload: dict) -> None:
        payload["routes"][0]["example"] = {"label": "", "measurements": []}

    def missing_scope_path_parameter(payload: dict) -> None:
        payload["routes"][0]["scoped"] = True
        payload["routes"][0]["scope_in"] = "path"
        payload["routes"][0]["scope_param"] = "device_id"

    def duplicate_action(payload: dict) -> None:
        payload["actions"].append(deepcopy(payload["actions"][0]))

    def unknown_required_action(payload: dict) -> None:
        payload["actions"] = []
        payload["routes"][0]["required_action"] = "does.not.exist"

    def missing_http_upstream_path(payload: dict) -> None:
        payload["routes"][0].pop("upstream_path")

    cases = [
        ("unknown_upstream", unknown_upstream),
        ("body_and_body_ref", body_and_body_ref),
        ("malformed_json_schema", malformed_json_schema),
        ("invalid_example", invalid_example),
        ("missing_scope_path_parameter", missing_scope_path_parameter),
        ("duplicate_action", duplicate_action),
        ("unknown_required_action", unknown_required_action),
        ("missing_http_upstream_path", missing_http_upstream_path),
    ]

    for label, mutate in cases:
        name = f"robust_invalid_{label}_{uuid4().hex[:6]}"
        registration = _robust_registration(name)
        mutate(registration)

        response = await client.post("/extensions", json=registration)
        assert response.status_code in (409, 422), (label, response.status_code, response.text)

        async with session_factory() as session:
            persisted = await session.scalar(select(Extension).where(Extension.name == name))
            assert persisted is None, label


async def test_concurrent_action_claim_has_one_owner_and_no_partial_loser(committed_client):
    client, session_factory = committed_client
    first_name = f"robust_owner_a_{uuid4().hex[:8]}"
    second_name = f"robust_owner_b_{uuid4().hex[:8]}"
    shared_action = f"robust.shared.{uuid4().hex[:10]}"
    registrations = []
    for name in (first_name, second_name):
        registration = _robust_registration(name)
        registration["actions"] = [{"name": shared_action, "description": "Contended action"}]
        registration["routes"][0]["required_action"] = shared_action
        registrations.append(registration)

    responses = await asyncio.gather(
        *(client.post("/extensions", json=registration) for registration in registrations)
    )
    assert sorted(response.status_code for response in responses) == [201, 409]

    async with session_factory() as session:
        extensions = list(
            (
                await session.scalars(
                    select(Extension).where(Extension.name.in_([first_name, second_name]))
                )
            ).all()
        )
        owners = list(
            (
                await session.scalars(
                    select(ExtensionAction).where(ExtensionAction.action_name == shared_action)
                )
            ).all()
        )
        assert len(extensions) == 1
        assert len(owners) == 1
        assert owners[0].extension_id == extensions[0].id

    assert (await client.delete(f"/extensions/{extensions[0].name}")).status_code == 204


@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("malformed_base_url", 400),
        ("unsupported_method", 400),
        ("relative_route_path", 400),
        ("unresolved_upstream_placeholder", 422),
        ("unknown_top_level_field", 400),
    ],
)
async def test_dangerous_manifest_shapes_are_rejected(committed_client, case, expected_status):
    client, session_factory = committed_client
    name = f"robust_boundary_{case}_{uuid4().hex[:6]}"
    registration = _robust_registration(name)

    if case == "malformed_base_url":
        registration["upstreams"]["primary-service"]["base_url"] = "not a URL"
    elif case == "unsupported_method":
        registration["routes"][0]["method"] = "NOT-A-METHOD"
    elif case == "relative_route_path":
        registration["routes"][0]["path"] = "missing-leading-slash/{widget_id}"
    elif case == "unresolved_upstream_placeholder":
        registration["routes"][0]["upstream_path"] = "/widgets/{missing_id}"
    elif case == "unknown_top_level_field":
        registration["upstreems"] = registration["upstreams"]

    response = await client.post("/extensions", json=registration)
    assert response.status_code == expected_status, (case, response.status_code, response.text)

    async with session_factory() as session:
        persisted = await session.scalar(select(Extension).where(Extension.name == name))
        assert persisted is None, case