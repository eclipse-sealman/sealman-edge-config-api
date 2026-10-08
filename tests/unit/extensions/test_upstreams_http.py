import httpx
import pytest
from starlette.requests import Request

from extensions.upstreams import http as http_upstream


def _make_request(method="GET", path="/x", query_string=b"", path_params=None, headers=None, body=b"", scheme="http", client=("203.0.113.5", 12345)):
    header_items = headers.items() if hasattr(headers, "items") else headers or []
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in header_items]
    scope = {
        "type": "http",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": raw_headers,
        "path_params": path_params or {},
        "client": client,
    }
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_resolve_upstream_path_fills_placeholders_from_path_params():
    request = _make_request(path_params={"device_name": "abc123", "unused": "z"})
    result = http_upstream._resolve_upstream_path("/devices/{device_name}/status", request)
    assert result == "/devices/abc123/status"


def test_resolve_upstream_path_percent_encodes_values():
    request = _make_request(path_params={"item": "x?admin=1#/../y"})
    result = http_upstream._resolve_upstream_path("/items/{item}/details", request)
    assert result == "/items/x%3Fadmin%3D1%23%2F..%2Fy/details"


@pytest.mark.parametrize("value", [".", ".."])
def test_resolve_upstream_path_rejects_dot_segments(value):
    from fastapi import HTTPException

    request = _make_request(path_params={"item": value})
    with pytest.raises(HTTPException) as exc_info:
        http_upstream._resolve_upstream_path("/items/{item}", request)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_dispatch_streams_response_body_without_buffering(monkeypatch):
    request = _make_request(method="GET", path="/foo", query_string=b"a=1&a=2", headers={"connection": "keep-alive"})

    class FakeStreamResponse:
        status_code = 201
        headers = httpx.Headers({"content-type": "text/plain", "transfer-encoding": "chunked"})

        async def aiter_raw(self):
            yield b"chunk-1-"
            yield b"chunk-2"

        async def aclose(self):
            self.closed = True

    fake_resp = FakeStreamResponse()
    captured = {}

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            captured["method"] = method
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return {"method": method, "url": url}

        async def send(self, req, stream=False):
            captured["stream"] = stream
            return fake_resp

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    upstream = {"base_url": "https://upstream.example.com"}
    route = {"upstream_path": "/foo"}

    response = await http_upstream.dispatch(request, upstream, route)

    assert captured["url"] == "https://upstream.example.com/foo"
    assert captured["stream"] is True
    assert ("a", "1") in captured["params"] and ("a", "2") in captured["params"]
    assert "connection" not in {name.lower() for name, _ in captured["headers"]}
    assert response.status_code == 201
    assert "transfer-encoding" not in {k.lower() for k in response.headers.keys()}

    chunks = [chunk async for chunk in response.body_iterator]
    assert chunks == [b"chunk-1-", b"chunk-2"]


@pytest.mark.asyncio
async def test_dispatch_returns_502_on_upstream_connection_error(monkeypatch):
    request = _make_request(method="GET", path="/foo")

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            return {}

        async def send(self, req, stream=False):
            raise httpx.RequestError("boom")

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    response = await http_upstream.dispatch(request, {"base_url": "https://down.example.com"}, {"upstream_path": "/foo"})
    assert response.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc, status_code, detail",
    [
        (httpx.ConnectError("secret-host refused"), 502, "Upstream unavailable"),
        (httpx.ReadTimeout("slow"), 504, "Upstream timed out"),
        (httpx.PoolTimeout("full"), 503, "Upstream busy"),
    ],
)
async def test_dispatch_maps_upstream_errors_without_leaking_details(monkeypatch, exc, status_code, detail):
    import json

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            return {}

        async def send(self, req, stream=False):
            raise exc

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    response = await http_upstream.dispatch(
        _make_request(), {"base_url": "https://internal.example"}, {"upstream_path": "/foo"}
    )
    assert response.status_code == status_code
    assert json.loads(response.body) == {"detail": detail}


class _CapturingClient:
    def __init__(self):
        self.captured = {}

    def build_request(self, method, url, params=None, headers=None, content=None):
        self.captured.update(headers=headers, content=content)
        return {}

    async def send(self, req, stream=False):
        class _Resp:
            status_code = 200
            headers = httpx.Headers({})

            async def aiter_raw(self):
                return
                yield  # pragma: no cover

            async def aclose(self):
                pass

        return _Resp()


@pytest.mark.asyncio
async def test_dispatch_sends_no_body_when_request_declares_none(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(http_upstream, "_client", client)

    await http_upstream.dispatch(_make_request(), {"base_url": "https://u"}, {"upstream_path": "/foo"})

    assert client.captured["content"] is None
    assert "content-length" not in {name for name, _ in client.captured["headers"]}


@pytest.mark.asyncio
async def test_dispatch_forwards_content_length_with_streamed_body(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(http_upstream, "_client", client)

    request = _make_request(method="POST", headers={"content-length": "3"}, body=b"abc")
    await http_upstream.dispatch(request, {"base_url": "https://u"}, {"upstream_path": "/foo"})

    assert ("content-length", "3") in client.captured["headers"]
    assert client.captured["content"] is not None


@pytest.mark.asyncio
async def test_dispatch_sends_already_buffered_body_as_bytes(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(http_upstream, "_client", client)

    request = _make_request(method="POST", headers={"content-length": "3"})
    await http_upstream.dispatch(request, {"base_url": "https://u"}, {"upstream_path": "/foo"}, body=b"abc")

    assert client.captured["content"] == b"abc"


@pytest.mark.asyncio
async def test_dispatch_adds_forwarding_headers_from_scratch(monkeypatch):
    request = _make_request(scheme="https", client=("198.51.100.9", 4711), headers={"host": "gateway.example.com"})

    class FakeStreamResponse:
        status_code = 200
        headers = httpx.Headers({})

        async def aiter_raw(self):
            return
            yield  # pragma: no cover

        async def aclose(self):
            pass

    captured = {}

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            captured["headers"] = headers
            return {}

        async def send(self, req, stream=False):
            return FakeStreamResponse()

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    await http_upstream.dispatch(request, {"base_url": "https://upstream.example.com"}, {"upstream_path": "/foo"})

    headers = httpx.Headers(captured["headers"])
    assert headers["x-forwarded-for"] == "198.51.100.9"
    assert headers["x-forwarded-host"] == "gateway.example.com"
    assert headers["x-forwarded-proto"] == "https"


@pytest.mark.asyncio
async def test_dispatch_replaces_a_preexisting_x_forwarded_for(monkeypatch):
    request = _make_request(client=("198.51.100.9", 4711), headers={"x-forwarded-for": "9.9.9.9"})

    class FakeStreamResponse:
        status_code = 200
        headers = httpx.Headers({})

        async def aiter_raw(self):
            return
            yield  # pragma: no cover

        async def aclose(self):
            pass

    captured = {}

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            captured["headers"] = headers
            return {}

        async def send(self, req, stream=False):
            return FakeStreamResponse()

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    await http_upstream.dispatch(request, {"base_url": "https://upstream.example.com"}, {"upstream_path": "/foo"})

    headers = httpx.Headers(captured["headers"])
    assert headers["x-forwarded-for"] == "198.51.100.9"


@pytest.mark.asyncio
async def test_dispatch_forwards_only_allowed_request_headers_and_preserves_repeats(monkeypatch):
    request = _make_request(
        headers=[
            ("Accept", "application/json"),
            ("X-Request-ID", "request-one"),
            ("X-Request-ID", "request-two"),
            ("Authorization", "Bearer platform-token"),
            ("Cookie", "session=secret"),
            ("X-Internal-Key", "internal-secret"),
            ("X-Custom", "not-allowed"),
        ]
    )

    class FakeStreamResponse:
        status_code = 200
        headers = httpx.Headers({})

        async def aiter_raw(self):
            return
            yield  # pragma: no cover

        async def aclose(self):
            pass

    captured = {}

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            captured["headers"] = headers
            return {}

        async def send(self, req, stream=False):
            return FakeStreamResponse()

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    await http_upstream.dispatch(request, {"base_url": "https://upstream.example.com"}, {"upstream_path": "/foo"})

    headers = httpx.Headers(captured["headers"])
    assert headers["accept"] == "application/json"
    assert headers.get_list("x-request-id") == ["request-one", "request-two"]
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert "x-internal-key" not in headers
    assert "x-custom" not in headers


@pytest.mark.asyncio
async def test_dispatch_strips_allowed_header_named_by_connection(monkeypatch):
    request = _make_request(headers={"Connection": "X-Request-ID", "X-Request-ID": "remove-me"})

    class FakeStreamResponse:
        status_code = 200
        headers = httpx.Headers({})

        async def aiter_raw(self):
            return
            yield  # pragma: no cover

        async def aclose(self):
            pass

    captured = {}

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            captured["headers"] = headers
            return {}

        async def send(self, req, stream=False):
            return FakeStreamResponse()

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    await http_upstream.dispatch(request, {"base_url": "https://upstream.example.com"}, {"upstream_path": "/foo"})

    assert "x-request-id" not in httpx.Headers(captured["headers"])


@pytest.mark.asyncio
async def test_dispatch_filters_response_headers_and_preserves_repeats(monkeypatch):
    request = _make_request()

    class FakeStreamResponse:
        status_code = 200
        headers = httpx.Headers(
            [
                ("Content-Type", "application/json"),
                ("Vary", "Accept-Encoding"),
                ("Vary", "Origin"),
                ("ETag", '"remove-me"'),
                ("Connection", "ETag"),
                ("Set-Cookie", "session=secret"),
                ("Server", "upstream"),
                ("Date", "Sun, 28 Sep 2026 00:00:00 GMT"),
                ("X-Custom", "not-allowed"),
            ]
        )

        async def aiter_raw(self):
            yield b"{}"

        async def aclose(self):
            pass

    class FakeClient:
        def build_request(self, method, url, params=None, headers=None, content=None):
            return {}

        async def send(self, req, stream=False):
            return FakeStreamResponse()

    monkeypatch.setattr(http_upstream, "_client", FakeClient())

    response = await http_upstream.dispatch(
        request,
        {"base_url": "https://upstream.example.com"},
        {"upstream_path": "/foo"},
    )

    assert response.headers["content-type"] == "application/json"
    assert response.headers.getlist("vary") == ["Accept-Encoding", "Origin"]
    for denied in ("etag", "connection", "set-cookie", "server", "date", "x-custom"):
        assert denied not in response.headers
