import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Generator

import asyncio
import pytest

from extensions import health
from extensions.upstreams import iotedge as iotedge_upstream


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # silence default stderr access log
        pass

    def do_GET(self):
        if self.path == "/health":
            self._write_json(200, {"version": "1.2.3"})
        elif self.path == "/health-bad-version":
            self._write_json(200, {"version": "9.9.9"})
        elif self.path == "/health-not-json":
            body = b"not json"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health-down":
            self._write_json(500, {"detail": "boom"})
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
def mock_health_server() -> Generator[str, None, None]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.asyncio
async def test_http_upstream_healthy(mock_health_server):
    upstream = {"type": "http", "base_url": mock_health_server, "health_path": "/health"}
    status, detail = await health.check_upstream_health(upstream)
    assert status == "healthy"
    assert detail is None


@pytest.mark.asyncio
async def test_http_upstream_healthy_with_matching_expected_version(mock_health_server):
    upstream = {
        "type": "http",
        "base_url": mock_health_server,
        "health_path": "/health",
        "version_field": "version",
        "expected_version": "1.2.3",
    }
    status, detail = await health.check_upstream_health(upstream)
    assert status == "healthy"
    assert detail is None


@pytest.mark.asyncio
async def test_http_upstream_healthy_with_satisfied_version_specifier(mock_health_server):
    upstream = {
        "type": "http",
        "base_url": mock_health_server,
        "health_path": "/health",
        "version_field": "version",
        "expected_version": ">=1.0.0,<2.0.0",
    }
    status, detail = await health.check_upstream_health(upstream)
    assert status == "healthy"
    assert detail is None


@pytest.mark.asyncio
async def test_http_upstream_unhealthy_on_unsatisfied_version_specifier(mock_health_server):
    upstream = {
        "type": "http",
        "base_url": mock_health_server,
        "health_path": "/health",
        "version_field": "version",
        "expected_version": ">=2.0.0",
    }
    status, detail = await health.check_upstream_health(upstream)
    assert status == "unhealthy"
    assert ">=2.0.0" in detail and "1.2.3" in detail


@pytest.mark.asyncio
async def test_http_upstream_unhealthy_on_version_mismatch(mock_health_server):
    upstream = {
        "type": "http",
        "base_url": mock_health_server,
        "health_path": "/health",
        "version_field": "version",
        "expected_version": "1.2.3",
    }
    upstream["health_path"] = "/health-bad-version"
    status, detail = await health.check_upstream_health(upstream)
    assert status == "unhealthy"
    assert "1.2.3" in detail and "9.9.9" in detail


@pytest.mark.asyncio
async def test_http_upstream_unhealthy_on_non_json_body_when_version_expected(mock_health_server):
    upstream = {
        "type": "http",
        "base_url": mock_health_server,
        "health_path": "/health-not-json",
        "expected_version": "1.2.3",
    }
    status, detail = await health.check_upstream_health(upstream)
    assert status == "unhealthy"
    assert "not valid JSON" in detail


@pytest.mark.asyncio
async def test_http_upstream_unhealthy_on_5xx(mock_health_server):
    upstream = {"type": "http", "base_url": mock_health_server, "health_path": "/health-down"}
    status, detail = await health.check_upstream_health(upstream)
    assert status == "unhealthy"
    assert "500" in detail


@pytest.mark.asyncio
async def test_http_upstream_unhealthy_on_connection_error():
    upstream = {"type": "http", "base_url": "http://127.0.0.1:1", "health_path": "/health"}
    status, detail = await health.check_upstream_health(upstream)
    assert status == "unhealthy"
    assert detail


@pytest.mark.asyncio
async def test_http_upstream_unknown_when_base_url_missing():
    status, detail = await health.check_upstream_health({"type": "http", "base_url": None})
    assert status == "unknown"


@pytest.mark.asyncio
async def test_iotedge_upstream_delegates_to_check_module_health(monkeypatch):
    async def fake_check_module_health(module_name, health_device_query):
        assert module_name == "mymodule"
        assert health_device_query == "tags.env='prod'"
        return "healthy", None

    monkeypatch.setattr(iotedge_upstream, "check_module_health", fake_check_module_health)

    upstream = {"type": "iotedge", "module_name": "mymodule", "health_device_query": "tags.env='prod'"}
    status, detail = await health.check_upstream_health(upstream)
    assert status == "healthy"
    assert detail is None


@pytest.mark.asyncio
async def test_unknown_upstream_type_reports_unknown():
    status, detail = await health.check_upstream_health({"type": "carrier_pigeon"})
    assert status == "unknown"
    assert detail


@pytest.mark.asyncio
async def test_slow_upstream_is_capped_at_health_check_timeout(monkeypatch):
    """A hung upstream (e.g. an iotedge canary that's unreachable, or an http upstream on
    a black-holed route) must not block the request past `_HEALTH_CHECK_TIMEOUT`, even
    though `_resolve_canary_agent_modules` alone could otherwise take up to ~30s."""
    monkeypatch.setattr(health, "_HEALTH_CHECK_TIMEOUT", 0.05)

    async def hangs_forever(module_name, health_device_query):
        await asyncio.sleep(10)
        return "healthy", None

    monkeypatch.setattr(iotedge_upstream, "check_module_health", hangs_forever)

    upstream = {"type": "iotedge", "module_name": "mymodule", "health_device_query": "tags.env='prod'"}
    status, detail = await health.check_upstream_health(upstream)
    assert status == "unknown"
    assert "timed out" in detail

