"""Turns persisted `extension_routes` rows into live FastAPI routes and back.

Current scope: route mounting/unmounting mechanics, the two visibility channels'
auth (`public` via the route's own `required_action` through `ABACPermissionCheck`,
`internal` via `X-Internal-Key`), the dynamic per-manifest request signature
(`extensions/signature.py`), `declared`-mode body validation (`extensions/body_validation.py`),
and real upstream dispatch (`http`/`iotedge`, see `extensions/upstreams/`).
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from starlette.responses import Response
from starlette.routing import BaseRoute

from authorization.abac_permission_check import ABACPermissionCheck
from db.repos.extension import ExtensionRepository
from db.session import get_repository
from exceptions import APIError
from helper import AuditTrail

from . import body_validation, signature
from .security import keys_match
from .upstreams import http as http_upstream
from .upstreams import iotedge as iotedge_upstream

logger = logging.getLogger("EdgeConfigAPI")

_ROUTE_NAME_PREFIX = "extension_route__"
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")

_DISPATCHERS: Dict[str, Callable] = {"http": http_upstream.dispatch, "iotedge": iotedge_upstream.dispatch}

BuiltRoutes = Dict[FastAPI, List[BaseRoute]]


def _route_name(extension_name: str, route_id: str) -> str:
    return f"{_ROUTE_NAME_PREFIX}{extension_name}__{route_id}"


def _operation_id(extension_name: str, method: str, path: str) -> str:
    """ Deterministic `{extension_name}_{method}_{path}` slugified,
    so two extensions (or two routes of one extension, which must already differ in
    method/path to both be routable) never collide on one `operation_id`, which FastAPI
    does not itself detect or reject."""
    slug = _NON_ALNUM_RE.sub("_", path).strip("_")
    return f"{extension_name}_{method.lower()}_{slug}"


def _internal_key_dependency(extension_name: str):
    async def _check(
        request: Request,
        x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key"),
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> None:
        access = f"internal {request.method} {request.url.path}"
        if not x_internal_key:
            await AuditTrail.log("unknown", f"{access} rejected: missing X-Internal-Key")
            raise HTTPException(status_code=401, detail="Missing X-Internal-Key")
        row = await extension_repo.get_extension_row(extension_name)
        if row is None or not keys_match(x_internal_key, row.get("internal_key_hash") or ""):
            await AuditTrail.log("unknown", f"{access} rejected: invalid X-Internal-Key")
            raise HTTPException(status_code=401, detail="Invalid X-Internal-Key")
        await AuditTrail.log(f"extension:{extension_name}", access)

    return _check


def _dispatch_handler(extension_name: str, upstream: Dict[str, Any], route: Dict[str, Any]):
    dispatch = _DISPATCHERS.get(upstream["type"])
    if dispatch is None:
        raise ValueError(f"Unknown upstream type '{upstream['type']}'")

    # Only `declared` mode ever validates a body; `upstream_declared`/`unreachable_ref`/
    # `none` all skip straight to dispatch with no JSON parsing at all.
    body_schema = route.get("body") if route.get("validation_mode") == "declared" else None
    # ABAC reads the last value while upstreams may read the first one.
    single_query_param = (
        (route.get("scope_param") or "device_id")
        if route.get("scoped") and (route.get("scope_in") or "query") == "query"
        else None
    )

    async def _handler(request: Request, **_path_and_query: Any) -> Response:
        if single_query_param and len(request.query_params.getlist(single_query_param)) > 1:
            raise HTTPException(status_code=400, detail=f"Query parameter '{single_query_param}' must not be repeated")
        # `declared` mode buffers the body (size-capped) to validate it against the
        # route's JSON Schema before dispatch, then hands those bytes to the dispatcher,
        # true zero-buffer streaming and request-body validation are mutually exclusive
        # for the same route, by construction, not an oversight.
        body = None
        if body_schema is not None:
            body = await body_validation.read_limited_body(request)
            payload = body_validation.parse_json(body)
            try:
                errors = body_validation.validate_payload(route["id"], body_schema, payload)
            except Exception as exc:  # $ref resolution failing closed, or similar
                logger.error(f"Body schema validation errored for {extension_name} {route.get('path')}: {exc}")
                raise HTTPException(status_code=500, detail="This route's body schema is misconfigured")
            if errors:
                raise HTTPException(status_code=422, detail=errors)
        try:
            return await dispatch(request, upstream, route, body=body)
        except APIError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    _handler.__signature__ = signature.build_signature(route)
    return _handler


def _app_for_visibility(visibility: str, public_app: FastAPI, internal_app: FastAPI) -> FastAPI:
    apps = {"public": public_app, "internal": internal_app}
    if visibility not in apps:
        raise ValueError(f"Unknown route visibility '{visibility}'")
    return apps[visibility]


def _add_routes_from_specs(
    public_app: FastAPI,
    internal_app: FastAPI,
    extension_name: str,
    upstreams: List[Dict[str, Any]],
    routes: List[Dict[str, Any]],
) -> None:
    """Appends one route per persisted `extension_routes` row for `extension_name` to
    the app matching its `visibility`, dispatching to the upstream (by manifest key)
    each route references. Only called by `build_routes`, on scratch route lists."""
    upstream_by_key = {upstream["key"]: upstream for upstream in upstreams}

    for route in routes:
        upstream = upstream_by_key.get(route["upstream"])
        if upstream is None:
            logger.warning(
                f"Skipping extension route {extension_name} {route.get('path')}: "
                f"unknown upstream key '{route['upstream']}'"
            )
            continue

        visibility = route.get("visibility") or "public"
        target_app = _app_for_visibility(visibility, public_app, internal_app)

        dependencies = []
        if visibility == "internal":
            dependencies.append(Depends(_internal_key_dependency(extension_name)))
        elif visibility == "public" and route.get("required_action"):
            # `scoped` drives ABAC device lookup, not just iotedge targeting.
            # Without it, holding `required_action` is enough for any device.
            if route.get("scoped"):
                dependencies.append(
                    Depends(
                        ABACPermissionCheck(
                            route["required_action"],
                            device_path=route.get("scope_param") or "device_id",
                            device_in=route.get("scope_in") or "query",
                        )
                    )
                )
            else:
                dependencies.append(Depends(ABACPermissionCheck(route["required_action"], device_path=None)))

        tags = list(route.get("tags") or []) + [f"Extension: {extension_name}"]
        method = route.get("method") or "GET"

        # Documents the route's raw JSON Schema body in /openapi.json only, merged
        # verbatim into the generated Operation Object, never bound as a native
        # FastAPI Body(...) param (see signature.py). Covers both `declared` (author-
        # supplied) and `upstream_declared` (fetched-from-upstream) bodies, since both
        # populate route["body"]; `none`/`unreachable_ref` routes leave this None.
        openapi_extra = None
        if route.get("body"):
            openapi_extra = {
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": route["body"],
                            **({"example": route["example"]} if route.get("example") else {}),
                        }
                    },
                }
            }

        target_app.add_api_route(
            route["path"],
            _dispatch_handler(extension_name, upstream, route),
            methods=[method],
            name=_route_name(extension_name, route["id"]),
            summary=route.get("summary") or f"{extension_name}: {method} {route['path']}",
            description=route.get("description"),
            tags=tags,
            deprecated=bool(route.get("deprecated")),
            status_code=route.get("status_code") or 200,
            operation_id=_operation_id(extension_name, method, route["path"]),
            dependencies=dependencies,
            include_in_schema=True,
            openapi_extra=openapi_extra,
        )
        mounted = target_app.router.routes[-1]
        mounted.extension_name = extension_name
        mounted.extension_route_id = route["id"]


def build_routes(
    public_app: FastAPI,
    internal_app: FastAPI,
    extension_name: str,
    upstreams: List[Dict[str, Any]],
    routes: List[Dict[str, Any]],
) -> BuiltRoutes:
    """Builds `extension_name`'s routes with each app's own router settings (global
    dependencies, dependency overrides) without publishing them. Raises on any route
    that cannot be built; the live route tables are untouched either way."""
    apps = (public_app, internal_app)
    originals = {app: app.router.routes for app in apps}
    for app in apps:
        app.router.routes = list(originals[app])
    try:
        _add_routes_from_specs(public_app, internal_app, extension_name, upstreams, routes)
        return {app: app.router.routes[len(originals[app]):] for app in apps}
    finally:
        for app in apps:
            app.router.routes = originals[app]


def publish_routes(
    public_app: FastAPI,
    internal_app: FastAPI,
    extension_name: str,
    built: Optional[BuiltRoutes] = None,
) -> None:
    """Replaces `extension_name`'s live routes with `built` (None only unmounts) and
    drops the replaced routes' cached body validators. Synchronous, so a concurrent
    request sees either the old or the new route set, never a mix. Matches routes on
    the owner stamped at build time, never on the route name, so `foo` cannot unmount
    `foo__bar`'s routes."""
    for app in (public_app, internal_app):
        kept = []
        for route in app.router.routes:
            if getattr(route, "extension_name", None) == extension_name:
                body_validation.invalidate(route.extension_route_id)
                continue
            kept.append(route)
        new_routes = (built or {}).get(app, [])
        app.router.routes = kept + new_routes
        app.openapi_schema = None
    count = sum(len(new_routes) for new_routes in (built or {}).values())
    logger.info(f"Published {count} live route(s) for extension '{extension_name}'")


async def load_all_routes(
    public_app: FastAPI,
    internal_app: FastAPI,
    extension_repo: ExtensionRepository,
) -> None:
    """Re-mounts every persisted, *enabled* extension's routes, called once at startup
    so registrations survive a process restart. A disabled extension stays disabled.
    One extension failing to build is logged and skipped; it must not abort startup."""
    for ext in await extension_repo.list_extensions_rows():
        if not ext.get("enabled"):
            continue
        upstreams = await extension_repo.list_upstreams(ext["name"])
        routes = await extension_repo.list_routes(ext["name"])
        try:
            built = build_routes(public_app, internal_app, ext["name"], upstreams, routes)
        except Exception:
            logger.exception(f"Could not build routes for extension '{ext['name']}'; it stays unmounted")
            continue
        publish_routes(public_app, internal_app, ext["name"], built)
