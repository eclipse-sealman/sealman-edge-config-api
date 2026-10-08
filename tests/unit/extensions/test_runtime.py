import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from extensions import runtime
from extensions.security import hash_key

UPSTREAMS = [{"key": "svc", "type": "http", "base_url": "http://svc"}]


def _route(route_id: str, path: str, **overrides) -> dict:
    return {
        "id": route_id,
        "upstream": "svc",
        "path": path,
        "method": "GET",
        "upstream_path": "/x",
        "visibility": "public",
        "query_params": [],
        **overrides,
    }


def _live_paths(app: FastAPI, extension_name: str) -> list:
    return [r.path for r in app.router.routes if getattr(r, "extension_name", None) == extension_name]


def _mount(public: FastAPI, internal: FastAPI, extension_name: str, routes: list) -> None:
    built = runtime.build_routes(public, internal, extension_name, UPSTREAMS, routes)
    runtime.publish_routes(public, internal, extension_name, built)


def test_build_routes_does_not_touch_live_route_tables():
    public, internal = FastAPI(), FastAPI()
    before = list(public.router.routes)

    built = runtime.build_routes(public, internal, "widgets", UPSTREAMS, [_route("r1", "/w")])

    assert public.router.routes == before
    assert [r.path for r in built[public]] == ["/w"]
    assert built[internal] == []


def test_failed_build_leaves_previous_live_routes_in_place():
    public, internal = FastAPI(), FastAPI()
    _mount(public, internal, "widgets", [_route("r1", "/old")])

    with pytest.raises(ValueError):
        runtime.build_routes(
            public, internal, "widgets", UPSTREAMS, [_route("r2", "/new"), _route("r3", "/bad", visibility="bogus")]
        )

    assert _live_paths(public, "widgets") == ["/old"]


def test_publish_replaces_and_unmounts_only_the_named_extension():
    public, internal = FastAPI(), FastAPI()
    _mount(public, internal, "foo", [_route("r1", "/foo/one")])
    _mount(public, internal, "foo__bar", [_route("r2", "/foo-bar/one")])

    _mount(public, internal, "foo", [_route("r3", "/foo/two")])
    assert _live_paths(public, "foo") == ["/foo/two"]
    assert _live_paths(public, "foo__bar") == ["/foo-bar/one"]

    runtime.publish_routes(public, internal, "foo", None)
    assert _live_paths(public, "foo") == []
    assert _live_paths(public, "foo__bar") == ["/foo-bar/one"]


class _FakeExtensionRepo:
    async def get_extension_row(self, name):
        return {"name": name, "internal_key_hash": hash_key("right-key")}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key, expected_status, expected_user",
    [(None, 401, "unknown"), ("wrong-key", 401, "unknown"), ("right-key", None, "extension:widgets")],
)
async def test_internal_key_dependency_authenticates_and_audits(monkeypatch, key, expected_status, expected_user):
    logged = []

    async def fake_log(user, access, method=None):
        logged.append((user, access))

    monkeypatch.setattr(runtime.AuditTrail, "log", fake_log)
    check = runtime._internal_key_dependency("widgets")
    request = Request({"type": "http", "method": "GET", "path": "/widgets/x", "headers": [], "query_string": b""})

    if expected_status is None:
        await check(request, x_internal_key=key, extension_repo=_FakeExtensionRepo())
    else:
        with pytest.raises(HTTPException) as exc_info:
            await check(request, x_internal_key=key, extension_repo=_FakeExtensionRepo())
        assert exc_info.value.status_code == expected_status

    assert logged and logged[0][0] == expected_user
    assert "internal GET /widgets/x" in logged[0][1]
