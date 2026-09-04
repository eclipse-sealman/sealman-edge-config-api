"""Runtime machinery that turns a stored extension route into a live FastAPI
route on one of three apps - selected by the route's ``visibility`` - and
keeps each app's OpenAPI schema (``/docs``) in sync.

Authorization is not reimplemented here: 'public' routes reuse the existing
``authorization.abac_permission_check.ABACPermissionCheck`` dependency
(identical RBAC + ABAC evaluation as every other router in this API);
'internal' and 'device' routes are authorized by a namespace-locked key
(``registry.verify_internal_key`` / ``registry.verify_device_key``).

Routes are built with a dynamically generated signature so that path
parameters, query parameters and a declared request body all show up
correctly in ``/docs`` and are validated by FastAPI before dispatch.
"""
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import jsonschema
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request

from authorization.abac_permission_check import ABACPermissionCheck
from db.repos.extension import ExtensionRepository
from db.session import get_repository

from . import registry
from .iotedge_transport import get_module_twin, invoke_direct_method, patch_module_twin
from .proxy import proxy_request

_PLACEHOLDER_RE = re.compile(r"{([^}]+)}")

_PY_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


@dataclass
class RouteRuntime:
    extension_name: str
    path: str
    method: str
    required_action: Optional[str]
    scoped: bool
    transport: str = "http"
    visibility: str = "public"
    upstream: Optional[str] = None
    # http transport
    base_url: Optional[str] = None
    upstream_path: Optional[str] = None
    # iotedge transport
    module_name: Optional[str] = None
    method_name: Optional[str] = None
    iotedge_operation: str = "direct_method"
    # scoping / shaping
    scope_param: str = "device_id"
    scope_in: str = "query"
    query_params: List[dict] = field(default_factory=list)
    body: Optional[dict] = None
    summary: Optional[str] = None
    description: Optional[str] = None


def _route_name(extension_name: str, method: str, path: str) -> str:
    """Deterministic, greppable route name so we can find/remove it later."""
    return f"ext::{extension_name}::{method}::{path}"


def _path_param_names(path: str) -> List[str]:
    return _PLACEHOLDER_RE.findall(path)


def _effective_query_params(rt: RouteRuntime) -> List[dict]:
    """Declared query params plus implicit ones the transport/scoping needs."""
    qps = list(rt.query_params or [])
    path_params = set(_path_param_names(rt.path))

    def _ensure(name, desc):
        if name not in path_params and not any(q.get("name") == name for q in qps):
            qps.append({"name": name, "type": "string", "required": True, "description": desc})

    if rt.visibility == "public" and rt.scoped and rt.scope_in == "query":
        _ensure(rt.scope_param, "Device identifier used for the ABAC scope check.")
    if rt.transport == "iotedge":
        needs_device = rt.visibility == "internal" or not rt.scoped
        if needs_device:
            _ensure("device_id", "Target device id the module runs on.")
    return qps


def _request_body_schema(rt: RouteRuntime) -> Optional[dict]:
    """Return the JSON Schema documenting/validating this route's request body."""
    if rt.body:
        return rt.body
    if rt.transport == "iotedge":
        if rt.iotedge_operation == "twin_read":
            return None
        if rt.iotedge_operation == "twin_write":
            return {
                "type": "object",
                "description": "Module-twin desired-properties patch (free-form JSON).",
            }
        return {"type": "object", "description": "Direct-method payload (free-form JSON)."}
    return None


def _openapi_extra(rt: RouteRuntime) -> Optional[dict]:
    schema = _request_body_schema(rt)
    if not schema:
        return None
    return {
        "requestBody": {
            "required": bool(rt.body),
            "content": {"application/json": {"schema": schema}},
        }
    }


def _validate_body(payload, rt: RouteRuntime) -> None:
    if not rt.body:
        return
    try:
        jsonschema.validate(payload, rt.body)
    except jsonschema.ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Body validation failed: {exc.message}")
    except jsonschema.SchemaError as exc:  # schema is checked at registration time
        raise HTTPException(status_code=500, detail=f"Invalid body schema: {exc.message}")


async def _extract_json_payload(request: Request):
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")


def _scope_device(rt: RouteRuntime, request: Request) -> Optional[str]:
    if not rt.scoped:
        return None
    if rt.scope_in == "path":
        return request.path_params.get(rt.scope_param)
    return request.query_params.get(rt.scope_param)


def _build_signature(rt: RouteRuntime, auth_default) -> inspect.Signature:
    keyed = rt.visibility in ("internal", "device")
    params = [
        inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
    ]

    for name in _path_param_names(rt.path):
        desc = None
        if not keyed and rt.scoped and rt.scope_in == "path" and name == rt.scope_param:
            desc = "Device identifier used for the ABAC scope check."
        elif rt.visibility == "internal" and name == "device_id":
            desc = "Target device id."
        params.append(inspect.Parameter(
            name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=Path(..., description=desc), annotation=str,
        ))

    for q in _effective_query_params(rt):
        py = _PY_TYPES.get(q.get("type", "string"), str)
        if q.get("required", True):
            params.append(inspect.Parameter(
                q["name"], inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=Query(..., description=q.get("description")), annotation=py,
            ))
        else:
            params.append(inspect.Parameter(
                q["name"], inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=Query(None, description=q.get("description")), annotation=Optional[py],
            ))

    if rt.visibility == "internal":
        params.append(inspect.Parameter(
            "x_internal_key", inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=Header(None, alias="X-Internal-Key",
                           description="Internal API key of the owning extension."),
            annotation=Optional[str],
        ))
        params.append(inspect.Parameter(
            "extension_repo", inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=Depends(get_repository(ExtensionRepository)),
        ))
    elif rt.visibility == "device":
        params.append(inspect.Parameter(
            "x_device_key", inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=Header(None, alias="X-Device-Key",
                           description="Per-device key issued at deployment."),
            annotation=Optional[str],
        ))
        params.append(inspect.Parameter(
            "extension_repo", inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=Depends(get_repository(ExtensionRepository)),
        ))
    else:
        params.append(inspect.Parameter(
            "auth", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=auth_default,
        ))
    return inspect.Signature(params)


def _make_public_endpoint(rt: RouteRuntime) -> Callable:
    """Public (user-facing) endpoint: RBAC + ABAC via ABACPermissionCheck."""
    permission_dep = ABACPermissionCheck(rt.required_action, device_path=rt.scope_param if rt.scoped else None)

    if rt.transport == "iotedge":
        async def endpoint(request: Request, auth=Depends(permission_dep), **_ignored):
            device_id = auth.get("device_id") or request.query_params.get("device_id")
            if not device_id:
                raise HTTPException(status_code=400, detail="Missing target 'device_id'")
            if rt.iotedge_operation == "twin_read":
                return await get_module_twin(device_id, rt.module_name)
            payload = await _extract_json_payload(request)
            _validate_body(payload, rt)
            if rt.iotedge_operation == "twin_write":
                return await patch_module_twin(device_id, rt.module_name, payload)
            return await invoke_direct_method(device_id, rt.module_name, rt.method_name, payload)
    else:
        async def endpoint(request: Request, auth=Depends(permission_dep), **_ignored):
            if rt.body:
                _validate_body(await _extract_json_payload(request), rt)
            return await proxy_request(
                rt.base_url, rt.upstream_path, rt.method, request, dict(request.path_params),
            )

    endpoint.__signature__ = _build_signature(rt, Depends(permission_dep))
    return endpoint


def _make_internal_endpoint(rt: RouteRuntime) -> Callable:
    """Internal (service-to-service) endpoint: authorized by X-Internal-Key,
    namespace-locked to the owning extension. No user token, no ABAC scope."""

    async def endpoint(
        request: Request,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
        **_ignored,
    ):
        await registry.verify_internal_key(
            extension_repo, rt.extension_name, request.headers.get("X-Internal-Key", "")
        )
        if rt.transport == "iotedge":
            device_id = request.path_params.get("device_id") or request.query_params.get("device_id")
            if not device_id:
                raise HTTPException(status_code=400, detail="Missing target 'device_id'")
            if rt.iotedge_operation == "twin_read":
                return await get_module_twin(device_id, rt.module_name)
            payload = await _extract_json_payload(request)
            _validate_body(payload, rt)
            if rt.iotedge_operation == "twin_write":
                return await patch_module_twin(device_id, rt.module_name, payload)
            return await invoke_direct_method(device_id, rt.module_name, rt.method_name, payload)
        if rt.body:
            _validate_body(await _extract_json_payload(request), rt)
        return await proxy_request(
            rt.base_url, rt.upstream_path, rt.method, request, dict(request.path_params),
        )

    endpoint.__signature__ = _build_signature(rt, None)
    return endpoint


def _make_field_endpoint(rt: RouteRuntime) -> Callable:
    """Field-ingress (device -> micro-service) endpoint: authorized by a
    per-device X-Device-Key. Always an 'http' upstream; resolved device id is
    forwarded to the micro-service as X-Device-Id."""

    async def endpoint(
        request: Request,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
        **_ignored,
    ):
        device_id = await registry.verify_device_key(
            extension_repo, rt.extension_name, request.headers.get("X-Device-Key", "")
        )
        if rt.body:
            _validate_body(await _extract_json_payload(request), rt)
        return await proxy_request(
            rt.base_url, rt.upstream_path, rt.method, request, dict(request.path_params),
            extra_headers={"X-Device-Id": device_id},
        )

    endpoint.__signature__ = _build_signature(rt, None)
    return endpoint


def add_live_route(app: FastAPI, rt: RouteRuntime) -> None:
    """Mount a proxied route on ``app`` and invalidate the cached OpenAPI
    schema so ``/docs`` picks it up immediately."""
    if rt.visibility == "internal":
        endpoint = _make_internal_endpoint(rt)
        tag, prefix = f"Internal: {rt.extension_name}", "internal"
    elif rt.visibility == "device":
        endpoint = _make_field_endpoint(rt)
        tag, prefix = f"Device: {rt.extension_name}", "device"
    else:
        endpoint = _make_public_endpoint(rt)
        tag, prefix = f"Extension: {rt.extension_name}", rt.extension_name

    app.add_api_route(
        rt.path,
        endpoint,
        methods=[rt.method],
        summary=rt.summary or f"[{prefix}] {rt.method} {rt.path}",
        description=rt.description,
        tags=[tag],
        name=_route_name(rt.extension_name, rt.method, rt.path),
        openapi_extra=_openapi_extra(rt),
    )
    app.openapi_schema = None  # force /openapi.json regeneration


def remove_live_routes(app: FastAPI, extension_name: str) -> int:
    """Remove every live route contributed by ``extension_name``."""
    prefix = f"ext::{extension_name}::"
    kept, removed = [], 0
    for route in app.router.routes:
        if getattr(route, "name", "").startswith(prefix):
            removed += 1
        else:
            kept.append(route)
    app.router.routes = kept
    app.openapi_schema = None
    return removed


def _runtime_from_dict(extension_name: str, r: dict) -> RouteRuntime:
    return RouteRuntime(
        extension_name=extension_name,
        path=r["path"],
        method=r["method"],
        required_action=r.get("required_action"),
        scoped=bool(r.get("scoped")),
        transport=r.get("transport") or "http",
        visibility=r.get("visibility") or "public",
        upstream=r.get("upstream_name") or r.get("upstream"),
        base_url=r.get("base_url"),
        upstream_path=r.get("upstream_path"),
        module_name=r.get("module_name"),
        method_name=r.get("method_name"),
        iotedge_operation=r.get("iotedge_operation") or "direct_method",
        scope_param=r.get("scope_param") or "device_id",
        scope_in=r.get("scope_in") or "query",
        query_params=r.get("query_params") or [],
        body=r.get("body"),
        summary=r.get("summary"),
        description=r.get("description"),
    )


def _app_for_visibility(visibility: str, public_app: FastAPI, internal_app: FastAPI, field_app: FastAPI) -> FastAPI:
    if visibility == "internal":
        return internal_app
    if visibility == "device":
        return field_app
    return public_app


def add_routes_from_specs(
    public_app: FastAPI,
    internal_app: FastAPI,
    field_app: FastAPI,
    extension_name: str,
    routes: List[dict],
    upstreams: dict,
) -> None:
    """Mount every route just registered for ``extension_name`` on the app
    matching its visibility, resolving the transport from ``upstreams``."""
    for r in routes:
        up_key = r.get("upstream_name") or r.get("upstream")
        up = upstreams.get(up_key) or {}
        enriched = dict(r)
        enriched["transport"] = up.get("type", "http")
        enriched["base_url"] = up.get("base_url")
        enriched["module_name"] = up.get("module_name")
        rt = _runtime_from_dict(extension_name, enriched)
        app = _app_for_visibility(rt.visibility, public_app, internal_app, field_app)
        add_live_route(app, rt)


async def load_all_routes(
    public_app: FastAPI, internal_app: FastAPI, field_app: FastAPI, extension_repo: ExtensionRepository
) -> None:
    """Re-hydrate every persisted route on startup so registrations survive a
    process restart."""
    for r in await extension_repo.all_routes():
        rt = _runtime_from_dict(r["extension_name"], r)
        app = _app_for_visibility(rt.visibility, public_app, internal_app, field_app)
        add_live_route(app, rt)
