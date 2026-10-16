"""
End-to-end coverage for real upstream dispatch (extensions/upstreams/http.py): unlike
test_extensions_management_routes.py (which only inspects the live route tables), these
tests actually call a mounted extension route through `main.app` via ASGITransport and
let it make a real outbound HTTP call to a local `http.server` instance — exercising the
full register -> enable -> dispatch path exactly as a real client would.

`iotedge` dispatch is not covered here: it always talks to a real IoT Hub REST endpoint,
so its call shapes are covered by mocked unit tests
(tests/unit/extensions/test_upstreams_iotedge.py) instead.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import AsyncGenerator, Generator

import httpx
import pytest

from extensions.upstreams import http as http_upstream
from tests.integration.test_extensions_management_routes import _unique_name


@pytest.fixture(autouse=True)
async def _fresh_http_upstream_client() -> AsyncGenerator[None, None]:
    """`http_upstream._client` is a module-level singleton bound to whichever event loop
    first uses it; pytest-asyncio gives each test its own loop, so reusing it across
    tests raises "Event loop is closed" on the second one. Recreate it per test —
    a real, single-event-loop-lifetime process never hits this."""
    previous = http_upstream._client
    http_upstream._client = httpx.AsyncClient()
    yield
    await http_upstream._client.aclose()
    http_upstream._client = previous



class _EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence default stderr access log
        pass

    def _write_json(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Vary", "Origin")
        self.send_header("Set-Cookie", "extension-session=secret")
        self.send_header("Connection", "keep-alive")  # hop-by-hop — must never be relayed
        self.end_headers()
        self.wfile.write(payload)

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
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        if self.path == "/ping":
            self._write_json(200, {"pong": True})
        elif self.path.startswith("/echo"):
            self._write_json(
                200,
                {
                    "path": self.path,
                    "x_request_id": self.headers.get("X-Request-ID"),
                    "x_extension_test": self.headers.get("X-Extension-Test"),
                    "authorization": self.headers.get("Authorization"),
                    "cookie": self.headers.get("Cookie"),
                    "connection_header_value": self.headers.get("Connection"),
                    "x_forwarded_for": self.headers.get("X-Forwarded-For"),
                    "x_forwarded_proto": self.headers.get("X-Forwarded-Proto"),
                },
            )
        else:
            self._write_json(404, {"detail": "not found"})

    def do_POST(self):
        raw = self._read_body()
        self._write_json(201, {"received": json.loads(raw) if raw else None})


@pytest.fixture
def mock_http_upstream() -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _registration(name: str, base_url: str, upstream_path: str = "/ping", method: str = "GET") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "dispatch test",
        "upstreams": {"svc": {"type": "http", "base_url": base_url}},
        "routes": [
            {
                "upstream": "svc",
                "path": f"/{name}/route",
                "method": method,
                "upstream_path": upstream_path,
                "visibility": "public",
            }
        ],
    }


class TestHttpUpstreamDispatch:
    async def test_enabled_route_proxies_get_to_real_upstream(self, client, mock_http_upstream):
        name = _unique_name("dispatch_ext")
        registration = _registration(name, mock_http_upstream)

        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.get(f"/{name}/route")

        assert response.status_code == 200
        assert response.json() == {"pong": True}

    async def test_dispatch_applies_request_header_contract(self, client, mock_http_upstream):
        name = _unique_name("dispatch_ext")
        registration = _registration(name, mock_http_upstream, upstream_path="/echo")

        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.get(
            f"/{name}/route",
            params={"a": "1"},
            headers={
                "X-Request-ID": "request-123",
                "X-Extension-Test": "not-allowed",
                "Authorization": "Bearer platform-token",
                "Cookie": "session=secret",
                "Connection": "close",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["x_request_id"] == "request-123"
        assert body["x_extension_test"] is None
        assert body["authorization"] is None
        assert body["cookie"] is None
        # httpx always injects its own default Connection header on the outbound leg
        # (keep-alive) regardless - the point is it's never the client's own "close"
        # value blindly relayed through.
        assert body["connection_header_value"] != "close"
        # RFC 7239: this hop must disclose the original client/scheme to the upstream.
        assert body["x_forwarded_for"]
        assert body["x_forwarded_proto"] in ("http", "https")

    async def test_dispatch_strips_hop_by_hop_response_headers(self, client, mock_http_upstream):
        name = _unique_name("dispatch_ext")
        registration = _registration(name, mock_http_upstream)

        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.get(f"/{name}/route")

        assert response.headers.get_list("vary") == ["Accept-Encoding", "Origin"]
        for denied in ("connection", "set-cookie", "server", "date"):
            assert denied not in response.headers

    async def test_dispatch_relays_upstream_error_status_and_body(self, client, mock_http_upstream):
        name = _unique_name("dispatch_ext")
        registration = _registration(name, mock_http_upstream, upstream_path="/missing")

        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.get(f"/{name}/route")

        assert response.status_code == 404
        assert response.json() == {"detail": "not found"}

    async def test_dispatch_forwards_post_body(self, client, mock_http_upstream):
        name = _unique_name("dispatch_ext")
        registration = _registration(name, mock_http_upstream, upstream_path="/ping", method="POST")

        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.post(f"/{name}/route", json={"foo": "bar"})

        assert response.status_code == 201
        assert response.json() == {"received": {"foo": "bar"}}

    async def test_unreachable_upstream_returns_502(self, client):
        name = _unique_name("dispatch_ext")
        # nothing listens here - a closed local port
        registration = _registration(name, "http://127.0.0.1:1")

        assert (await client.post("/extensions", json=registration)).status_code == 201
        assert (await client.post(f"/extensions/{name}/enable")).status_code == 200

        response = await client.get(f"/{name}/route")

        assert response.status_code == 502
