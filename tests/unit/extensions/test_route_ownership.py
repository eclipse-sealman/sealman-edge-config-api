import asyncio
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from exceptions import APIError
from extensions import runtime, setup

UPSTREAMS = [{"key": "svc", "type": "http", "base_url": "http://svc"}]


def _route(path="/shared", method="GET", visibility="public", route_id="route"):
    return {
        "id": route_id,
        "path": path,
        "method": method,
        "visibility": visibility,
        "upstream": "svc",
        "upstream_path": "/upstream",
        "query_params": [],
    }


def _mount(name, routes):
    built = runtime.build_routes(setup._public_app, setup.internal_app, name, UPSTREAMS, routes)
    runtime.publish_routes(setup._public_app, setup.internal_app, name, built)


class _RouteRepo:
    def __init__(self, enabled, routes):
        self.extensions = {name: {"name": name, "enabled": value} for name, value in enabled.items()}
        self.routes = routes
        self.enabled_writes = []

    async def get_extension_row(self, name):
        return self.extensions.get(name)

    async def list_routes(self, name):
        return list(self.routes.get(name, []))

    async def list_upstreams(self, name):
        return UPSTREAMS


async def _enable_endpoint():
    setup._extension_locks = defaultdict(asyncio.Lock)
    setup._route_ownership_lock = asyncio.Lock()
    app = FastAPI()
    setup.setup_extensions(app)
    route = next(route for route in app.routes if route.path == "/extensions/{name}/enable")
    return route.endpoint


async def _replace_endpoint():
    setup._extension_locks = defaultdict(asyncio.Lock)
    setup._route_ownership_lock = asyncio.Lock()
    app = FastAPI()
    setup.setup_extensions(app)
    route = next(route for route in app.routes if route.path == "/extensions/{name}" and "PUT" in route.methods)
    return route.endpoint


@pytest.mark.asyncio
async def test_route_ownership_uses_exact_visibility_method_and_path():
    await _enable_endpoint()
    _mount("owner", [_route("/exact", "GET", "public")])

    setup._check_route_ownership("candidate", [_route("/exact", "POST", "public")])
    setup._check_route_ownership("candidate", [_route("/exact", "GET", "internal")])
    setup._check_route_ownership("candidate", [_route("/Exact", "GET", "public")])
    setup._check_route_ownership("candidate", [_route("/exact", "GET", "internal")])
    setup._check_route_ownership("owner", [_route("/exact", "GET", "public")])

    with pytest.raises(APIError, match="already owned by extension 'owner'") as exc_info:
        setup._check_route_ownership("candidate", [_route("/exact", "GET", "public")])
    assert exc_info.value.status_code == 409

    runtime.publish_routes(setup._public_app, setup.internal_app, "owner", None)
    setup._check_route_ownership("candidate", [_route("/exact", "GET", "public")])


@pytest.mark.asyncio
async def test_conflicting_enable_fails_before_schema_refresh_or_enabled_state_change(monkeypatch):
    endpoint = await _enable_endpoint()
    repo = _RouteRepo(
        {"established": True, "candidate": False},
        {"established": [_route()], "candidate": [_route()]},
    )
    _mount("established", repo.routes["established"])
    refresh = AsyncMock()
    check = AsyncMock()
    monkeypatch.setattr(setup.registry, "refresh_extension_ref_schemas", refresh)
    monkeypatch.setattr(setup.registry, "check_module_schema_ownership", check)

    with pytest.raises(APIError) as exc_info:
        await endpoint("candidate", repo)

    assert exc_info.value.status_code == 409
    assert repo.extensions["candidate"]["enabled"] is False
    refresh.assert_not_awaited()
    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_conflicting_enables_have_one_winner(monkeypatch):
    endpoint = await _enable_endpoint()
    repo = _RouteRepo(
        {"alpha": False, "beta": False},
        {"alpha": [_route(route_id="alpha-route")], "beta": [_route(route_id="beta-route")]},
    )
    monkeypatch.setattr(setup.registry, "check_module_schema_ownership", _async_noop)
    monkeypatch.setattr(setup.registry, "refresh_extension_ref_schemas", _async_noop)
    monkeypatch.setattr(setup, "_activate_module_schemas", _async_noop)

    async def enable(_repo, name):
        repo.extensions[name]["enabled"] = True
        repo.enabled_writes.append(name)
        return name

    monkeypatch.setattr(setup.registry, "enable_extension", enable)
    results = await asyncio.gather(endpoint("alpha", repo), endpoint("beta", repo), return_exceptions=True)

    assert len([result for result in results if isinstance(result, APIError) and result.status_code == 409]) == 1
    assert len(repo.enabled_writes) == 1
    assert sum(extension["enabled"] for extension in repo.extensions.values()) == 1


@pytest.mark.asyncio
async def test_enabled_replacement_conflict_is_rejected_before_registry_writes(monkeypatch):
    endpoint = await _replace_endpoint()
    repo = _RouteRepo(
        {"established": True, "candidate": True},
        {"established": [_route()], "candidate": [_route("/old", route_id="old-route")]},
    )
    _mount("established", repo.routes["established"])
    registration = _registration("candidate", [_route()])
    replace = AsyncMock()
    monkeypatch.setattr(setup.registry, "replace_extension", replace)

    with pytest.raises(APIError) as exc_info:
        await endpoint("candidate", registration, repo)

    assert exc_info.value.status_code == 409
    assert repo.routes["candidate"] == [_route("/old", route_id="old-route")]
    replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_enable_and_replacement_cannot_both_claim_the_route(monkeypatch):
    enable = await _enable_endpoint()
    replace = await _replace_endpoint()
    repo = _RouteRepo(
        {"alpha": True, "beta": False},
        {"alpha": [_route("/old", route_id="old-route")], "beta": [_route(route_id="beta-route")]},
    )
    _mount("alpha", repo.routes["alpha"])
    registration = _registration("alpha", [_route(route_id="replacement-route")])
    monkeypatch.setattr(setup.registry, "check_module_schema_ownership", _async_noop)
    monkeypatch.setattr(setup.registry, "refresh_extension_ref_schemas", _async_noop)
    monkeypatch.setattr(setup, "_activate_module_schemas", _async_noop)

    async def enable_extension(_repo, name):
        repo.extensions[name]["enabled"] = True
        return name

    async def replace_extension(_repo, name, proposed):
        repo.routes[name] = [route.model_dump() for route in proposed.routes]
        return SimpleNamespace(enabled=True)

    monkeypatch.setattr(setup.registry, "enable_extension", enable_extension)
    monkeypatch.setattr(setup.registry, "replace_extension", replace_extension)
    results = await asyncio.gather(enable("beta", repo), replace("alpha", registration, repo), return_exceptions=True)

    conflicts = [result for result in results if isinstance(result, APIError) and result.status_code == 409]
    assert len(conflicts) == 1
    claimed = [
        name
        for name, routes in repo.routes.items()
        if routes and routes[0]["path"] == "/shared" and repo.extensions[name]["enabled"]
    ]
    assert len(claimed) == 1


def _registration(name, routes):
    return SimpleNamespace(name=name, routes=[SimpleNamespace(model_dump=lambda route=route: route) for route in routes])


async def _async_noop(*args, **kwargs):
    return None
