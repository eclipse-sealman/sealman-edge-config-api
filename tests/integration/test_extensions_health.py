"""
Integration coverage for Phase 5 (extension health visibility): `POST
/extensions/{name}/health-check` triggers a fresh health check of each upstream and
persists the result (`extension_upstreams.last_status`/`last_detail`/`last_checked_at`),
while every other route (register/replace/enable/disable, and plain `GET`s) never does —
a `GET` only ever returns whatever was last persisted there.

`http` upstreams are checked against a real local `http.server` (no mocked `httpx`);
`iotedge` upstreams with no `health_device_query` configured are checked without any
network call at all (see `extensions/upstreams/iotedge.py::check_module_health`), so no
real/mocked IoT Hub call is needed here.
"""
import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Generator
from uuid import uuid4

import pytest

from tests.integration.test_extensions_management_routes import _unique_name


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence default stderr access log
        pass

    def do_GET(self):
        if self.path == "/health":
            time.sleep(getattr(self.server, "delay", 0))
            self._write_json(200, {"version": "1.0.0"})
        else:
            self._write_json(404, {"detail": "not found"})

    def _write_json(self, status: int, body: dict):
        import json

        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def mock_health_upstream() -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.delay = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def _slow_health_upstream(delay: float) -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.delay = delay
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _http_registration(name: str, base_url: str) -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "description": "health demo",
        "upstreams": {"svc": {"type": "http", "base_url": base_url}},
        "routes": [
            {"upstream": "svc", "path": f"/{name}/ping", "method": "GET", "upstream_path": "/ping"},
        ],
    }


def _iotedge_registration(name: str, health_device_query: str = None) -> dict:
    upstream = {"type": "iotedge", "module_name": "mymodule"}
    if health_device_query is not None:
        upstream["health_device_query"] = health_device_query
    return {
        "schema_version": 1,
        "name": name,
        "description": "iotedge health demo",
        "upstreams": {"edge": upstream},
        "routes": [
            {
                "upstream": "edge",
                "path": f"/{name}/ping",
                "method": "POST",
                "iotedge": {"operation": "direct_method", "method_name": "ping"},
                "required_action": "device.module.execute_method",
                "scoped": True,
            }
        ],
    }


class TestExtensionHealthVisibility:
    async def test_register_never_triggers_a_health_check(self, client, mock_health_upstream):
        name = _unique_name("health_register")
        response = await client.post("/extensions", json=_http_registration(name, mock_health_upstream))
        assert response.status_code == 201
        upstream = response.json()["upstreams"]["svc"]
        assert upstream["last_status"] == "unknown"
        # BaseAPIRouter's response_model_exclude_none=True drops a None field entirely.
        assert upstream.get("last_checked_at") is None

    async def test_get_detail_never_triggers_a_health_check(self, client, mock_health_upstream):
        name = _unique_name("health_get_no_check")
        await client.post("/extensions", json=_http_registration(name, mock_health_upstream))

        response = await client.get(f"/extensions/{name}")
        assert response.status_code == 200
        upstream = response.json()["upstreams"]["svc"]
        assert upstream["last_status"] == "unknown"
        assert upstream.get("last_checked_at") is None

    async def test_health_check_endpoint_checks_a_reachable_http_upstream_and_persists_healthy(
        self, client, mock_health_upstream
    ):
        name = _unique_name("health_ok")
        await client.post("/extensions", json=_http_registration(name, mock_health_upstream))

        response = await client.post(f"/extensions/{name}/health-check")
        assert response.status_code == 200
        upstream = response.json()["upstreams"]["svc"]
        assert upstream["last_status"] == "healthy"
        assert upstream.get("last_detail") is None
        assert upstream["last_checked_at"] is not None

        # a subsequent plain GET just returns the persisted result, no re-check needed
        get_response = await client.get(f"/extensions/{name}")
        assert get_response.json()["upstreams"]["svc"]["last_status"] == "healthy"

    async def test_health_check_endpoint_checks_an_unreachable_http_upstream_and_persists_unhealthy(self, client):
        name = _unique_name("health_down")
        await client.post("/extensions", json=_http_registration(name, "http://127.0.0.1:1"))

        response = await client.post(f"/extensions/{name}/health-check")
        assert response.status_code == 200
        upstream = response.json()["upstreams"]["svc"]
        assert upstream["last_status"] == "unhealthy"
        assert upstream["last_detail"]

    async def test_list_extensions_never_triggers_a_health_check(self, client, mock_health_upstream):
        name = _unique_name("health_list")
        await client.post("/extensions", json=_http_registration(name, mock_health_upstream))
        await client.post(f"/extensions/{name}/health-check")

        response = await client.get("/extensions")
        assert response.status_code == 200
        entry = next(e for e in response.json() if e["name"] == name)
        # still reflects the health-check call above, not a fresh (re-)check on list
        assert entry["upstreams"]["svc"]["last_status"] == "healthy"

    async def test_iotedge_upstream_with_no_health_device_query_is_always_unknown(self, client):
        name = _unique_name("health_iotedge_unconfigured")
        await client.post("/extensions", json=_iotedge_registration(name))

        response = await client.post(f"/extensions/{name}/health-check")
        assert response.status_code == 200
        upstream = response.json()["upstreams"]["edge"]
        assert upstream["last_status"] == "unknown"
        assert upstream["last_checked_at"] is not None  # a check did run, it just couldn't resolve a canary

    async def test_health_checks_for_one_extensions_upstreams_run_in_parallel(self, client):
        """Two upstreams, each taking ~0.3s to respond: if checked sequentially the
        request would take >=0.6s; run in parallel it should take close to 0.3s."""
        delay = 0.3
        server_a, thread_a = _slow_health_upstream(delay)
        server_b, thread_b = _slow_health_upstream(delay)
        try:
            base_a = f"http://127.0.0.1:{server_a.server_port}"
            base_b = f"http://127.0.0.1:{server_b.server_port}"
            name = _unique_name("health_parallel")
            registration = {
                "schema_version": 1,
                "name": name,
                "description": "parallel health demo",
                "upstreams": {
                    "a": {"type": "http", "base_url": base_a},
                    "b": {"type": "http", "base_url": base_b},
                },
                "routes": [
                    {"upstream": "a", "path": f"/{name}/a", "method": "GET", "upstream_path": "/ping"},
                    {"upstream": "b", "path": f"/{name}/b", "method": "GET", "upstream_path": "/ping"},
                ],
            }
            await client.post("/extensions", json=registration)

            start = time.monotonic()
            response = await client.post(f"/extensions/{name}/health-check")
            elapsed = time.monotonic() - start

            assert response.status_code == 200
            assert response.json()["upstreams"]["a"]["last_status"] == "healthy"
            assert response.json()["upstreams"]["b"]["last_status"] == "healthy"
            # elapsed should approximate the single slowest check (delay), not the sum of both
            assert elapsed < delay * 2
        finally:
            server_a.shutdown()
            server_b.shutdown()
            thread_a.join()
            thread_b.join()
