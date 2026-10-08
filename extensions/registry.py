"""Business logic for the extension registry: validates a manifest, enrolls its
actions into the existing RBAC catalog (`actions` table), persists the
extension + its upstreams/routes, and issues/rotates its keys.

Registration does not grant those actions to roles. Role assignment stays in the
normal RBAC UI. Deregister still strips an extension's actions from every role
so a deleted extension does not leave grants behind.

Owns *what* a registration means, not *how* a route runs (that's runtime.py) or how
it's bound to a dynamic FastAPI signature (a later stage's scope). Every function here
is a plain async function taking the repositories it needs as parameters, no
module-level state, so it can be exercised the same way from the management router
(DI) or from tests.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from jsonschema.exceptions import SchemaError

from db.repos.extension import ExtensionActionConflictError, ExtensionRepository
from db.repos.role import RoleRepository
from exceptions import APIError

from . import body_validation, health, signature
from .schemas import ExtensionHealthCheckResult, ExtensionRegistration, ExtensionDetail, RouteSpec, UpstreamSpec
from .security import generate_key, hash_key
from .upstreams import http as http_upstream
from .upstreams.iotedge import probe_registration_health

logger = logging.getLogger("EdgeConfigAPI")

_PATH_PARAM_RE = re.compile(r"{([^}]+)}")


def _validate_manifest_cross_references(registration: ExtensionRegistration) -> None:
    """Manifest-wide rules `schemas.py` can't express on its own (it validates each field
    in isolation): a route's `upstream` must exist, the fields it carries must match that
    upstream's type, and a `declared`-mode `body`/`example` pair must actually be a valid
    JSON Schema and a conforming example. No network calls happen here. Resolving a
    `body_ref` against its upstream's live OpenAPI doc is `_resolve_route_validation`'s
    job, run by `_prepare_persistence` before the write transaction."""
    seen_actions = set()
    duplicate_actions = set()
    for action in registration.actions:
        if action.name in seen_actions:
            duplicate_actions.add(action.name)
        seen_actions.add(action.name)
    if duplicate_actions:
        raise APIError(f"Duplicate action names in manifest: {sorted(duplicate_actions)}", 422)

    for route in registration.routes:
        upstream = registration.upstreams.get(route.upstream)
        if upstream is None:
            raise APIError(f"Route '{route.path}' references unknown upstream '{route.upstream}'", 422)

        if upstream.type == "http" and not route.upstream_path:
            raise APIError(f"Route '{route.path}' targets an http upstream but has no upstream_path", 422)

        if route.upstream_path:
            route_params = set(_PATH_PARAM_RE.findall(route.path))
            upstream_params = set(_PATH_PARAM_RE.findall(route.upstream_path))
            missing_params = upstream_params - route_params
            if missing_params:
                raise APIError(
                    f"Route '{route.path}' cannot supply upstream placeholders: {sorted(missing_params)}",
                    422,
                )

        if upstream.type == "iotedge" and route.iotedge is None:
            raise APIError(f"Route '{route.path}' targets an iotedge upstream but has no iotedge call spec", 422)

        if route.visibility == "public" and route.scoped and not route.required_action:
            raise APIError(
                f"Scoped public route '{route.path}' must set required_action; ABAC scopes are evaluated per action",
                422,
            )

        if route.visibility == "public" and upstream.type == "iotedge" and not route.scoped:
            raise APIError(
                f"Public iotedge route '{route.path}' must be scoped so ABAC checks the target device", 422
            )

        if route.body_ref and upstream.type != "http":
            raise APIError(
                f"Route '{route.path}' sets body_ref but its upstream '{route.upstream}' is not an http upstream", 422
            )

        if route.body is not None and route.body_ref:
            raise APIError(f"Route '{route.path}' cannot set both 'body' (declared schema) and 'body_ref'", 422)

        if route.scoped and route.scope_in == "path":
            path_params = set(_PATH_PARAM_RE.findall(route.path))
            if route.scope_param not in path_params:
                raise APIError(
                    f"Route '{route.path}' is path-scoped on '{route.scope_param}' "
                    f"but the path has no '{{{route.scope_param}}}' parameter",
                    422,
                )

        if route.scoped and route.scope_in == "query":
            scope_parameters = [parameter for parameter in route.query_params if parameter.name == route.scope_param]
            if any(not parameter.required for parameter in scope_parameters):
                raise APIError(
                    f"Route '{route.path}' query scope parameter '{route.scope_param}' must be required",
                    422,
                )

        try:
            signature.build_signature(route.model_dump())
        except ValueError as exc:
            raise APIError(f"Route '{route.path}' cannot be mounted: {exc}", 422)

        if route.body is not None:
            try:
                body_validation.check_schema(route.body)
            except SchemaError as exc:
                raise APIError(f"Route '{route.path}' body is not a valid JSON Schema: {exc.message}", 422)
            if route.example is not None:
                errors = body_validation.validate_once(route.body, route.example)
                if errors:
                    raise APIError(
                        f"Route '{route.path}' example does not conform to its own body schema: {errors}", 422
                    )


async def _validate_action_ownership(
    extension_repo: ExtensionRepository,
    registration: ExtensionRegistration,
    current_extension: Optional[str] = None,
) -> None:
    manifest_actions = {action.name for action in registration.actions}
    for action_name in manifest_actions:
        owner = await extension_repo.get_action_owner(action_name)
        if owner is not None and owner != current_extension:
            raise APIError(f"Action '{action_name}' is already owned by extension '{owner}'", 409)
        if owner is None and await extension_repo.action_exists(action_name):
            raise APIError(f"Action '{action_name}' is a platform action and cannot be extension-owned", 409)

    for route in registration.routes:
        action_name = route.required_action
        if not action_name or action_name in manifest_actions:
            continue
        owner = await extension_repo.get_action_owner(action_name)
        if owner is not None:
            raise APIError(
                f"Route '{route.path}' references action '{action_name}' owned by extension '{owner}'",
                409,
            )
        if not await extension_repo.action_exists(action_name):
            raise APIError(f"Route '{route.path}' references unknown action '{action_name}'", 422)


async def _fetch_openapi_documents(base_urls: List[str]) -> Dict[str, Optional[dict]]:
    """Fetches each distinct upstream's OpenAPI document once, all concurrently, so the
    total wait is bounded by the slowest single fetch rather than the sum."""
    unique = sorted(set(base_urls))
    documents = await asyncio.gather(*(http_upstream.fetch_openapi_document(url) for url in unique))
    return dict(zip(unique, documents))


def _resolve_route_validation(
    route: RouteSpec, upstreams: Dict[str, UpstreamSpec], documents: Dict[str, Optional[dict]]
) -> Tuple[str, Optional[dict]]:
    """Computes a route's `validation_mode` and the `body` snapshot to persist for it.

    `unreachable_ref` is not a value an author can request: it is the persisted result
    of a failed fetch of the upstream's own `{base_url}/openapi.json`. The author sets
    `body_ref: true` (or a literal `body`). This function, and `_apply_ref_schemas`,
    are the only writers of that status.
    """
    if route.body is not None:
        return "declared", route.body
    if route.body_ref:
        base_url = upstreams[route.upstream].base_url
        fetched = http_upstream.body_schema_from_document(documents.get(base_url), route.upstream_path, route.method)
        return ("upstream_declared", fetched) if fetched is not None else ("unreachable_ref", None)
    return "none", None


async def _prepare_persistence(registration: ExtensionRegistration) -> Tuple[List[dict], List[dict]]:
    """Resolves body schemas (network) and returns rows ready to insert. No writes.
    Callers must run this before opening a database transaction."""
    upstreams = [
        {"key": key, **spec.model_dump()} for key, spec in registration.upstreams.items()
    ]
    documents = await _fetch_openapi_documents(
        [registration.upstreams[route.upstream].base_url for route in registration.routes if route.body_ref]
    )
    routes = []
    for route in registration.routes:
        route_dict = route.model_dump()
        validation_mode, body_snapshot = _resolve_route_validation(route, registration.upstreams, documents)
        route_dict["validation_mode"] = validation_mode
        route_dict["body"] = body_snapshot
        routes.append(route_dict)
    return upstreams, routes


async def _load_ref_routes(extension_repo: ExtensionRepository, name: str) -> List[Tuple[dict, str]]:
    """Returns `(route, base_url)` for each of `name`'s persisted `body_ref` http routes."""
    upstream_by_key = {row["key"]: row for row in await extension_repo.list_upstreams(name)}
    pending = []
    for route in await extension_repo.list_routes(name):
        upstream = upstream_by_key.get(route["upstream"])
        if route.get("body_ref") and upstream is not None and upstream.get("type") == "http":
            pending.append((route, upstream["base_url"]))
    return pending


async def _refresh_pending(extension_repo: ExtensionRepository, pending: List[Tuple[dict, str]]) -> None:
    """Closes the read transaction before any network call, so a slow OpenAPI fetch
    never holds a pooled connection, then writes every result in one transaction."""
    await extension_repo.release_connection()
    if not pending:
        return
    documents = await _fetch_openapi_documents([base_url for _, base_url in pending])
    async with extension_repo.atomic():
        for route, base_url in pending:
            fetched = http_upstream.body_schema_from_document(
                documents.get(base_url), route["upstream_path"], route["method"]
            )
            mode = "upstream_declared" if fetched is not None else "unreachable_ref"
            await extension_repo.update_route_validation(route["id"], mode, fetched)


async def refresh_extension_ref_schemas(extension_repo: ExtensionRepository, name: str) -> None:
    """Re-fetches one extension's `body_ref` schemas and stores the result.

    `unreachable_ref` is a temporary persisted status, not a registration type. It
    flips back to `upstream_declared` the next time a refresh runs and the upstream's
    `/openapi.json` answers. Official triggers, and only these:

    - POST and PUT, via `_prepare_persistence`, once per save
    - process startup (`refresh_ref_schemas`, enabled extensions only)
    - `POST /extensions/{name}/enable`, including a disable-then-enable retry
    """
    if await extension_repo.get_extension_row(name) is None:
        return
    await _refresh_pending(extension_repo, await _load_ref_routes(extension_repo, name))


async def refresh_ref_schemas(extension_repo: ExtensionRepository) -> None:
    """Startup pass over every *enabled* extension. Disabled ones refresh on enable."""
    pending = []
    for ext in await extension_repo.list_extensions_rows():
        if ext.get("enabled"):
            pending.extend(await _load_ref_routes(extension_repo, ext["name"]))
    await _refresh_pending(extension_repo, pending)


async def _probe_iotedge_upstreams(registration: ExtensionRegistration) -> None:
    """Best-effort registration-time typo guard: for every `iotedge` upstream with a
    `health_device_query` set, resolve a canary device and confirm `module_name` shows
    up in its reported `$edgeAgent` modules, logged as a warning, never rejects
    registration (see extensions/upstreams/iotedge.py's probe_registration_health)."""
    for key, upstream in registration.upstreams.items():
        if upstream.type != "iotedge" or not upstream.health_device_query:
            continue
        warning = await probe_registration_health(upstream.module_name, upstream.health_device_query)
        if warning:
            logger.warning(f"Extension '{registration.name}' upstream '{key}': {warning}")


def _action_is_global(action_name: str, routes: List[RouteSpec]) -> bool:
    """An action is device-scoped only if every public route requiring it is scoped;
    a single unscoped route lets holders use it regardless of team scope."""
    uses = [route for route in routes if route.visibility == "public" and route.required_action == action_name]
    return not uses or not all(route.scoped for route in uses)


async def _enroll_actions(
    extension_repo: ExtensionRepository,
    registration: ExtensionRegistration,
) -> None:
    """Records the manifest's actions in the catalog. Does not grant them to any role."""
    for action in registration.actions:
        await extension_repo.upsert_action(
            action.name, action.description, is_global=_action_is_global(action.name, registration.routes)
        )
    await extension_repo.record_extension_actions(
        registration.name, [action.name for action in registration.actions]
    )


async def _strip_actions_from_all_roles(role_repo: RoleRepository, action_names: List[str]) -> None:
    """Revokes each action from every role that currently holds it. Used by DELETE so
    the action catalog can actually be cleaned up; PUT still refuses instead."""
    if not action_names:
        return
    for role in await role_repo.list_roles():
        held = set(role.get("actions") or [])
        for action_name in action_names:
            if action_name in held:
                await role_repo.remove_action_from_role(role["id"], action_name)


async def _build_detail(extension_repo: ExtensionRepository, name: str) -> ExtensionDetail:
    ext = await extension_repo.get_extension_row(name)
    if ext is None:
        raise APIError(f"Extension '{name}' not found", 404)

    upstreams_rows = await extension_repo.list_upstreams(name)
    routes_rows = await extension_repo.list_routes(name)
    actions = await extension_repo.list_extension_action_specs(name)

    upstreams = {row["key"]: {k: v for k, v in row.items() if k not in ("id", "key")} for row in upstreams_rows}
    routes = []
    for row in routes_rows:
        route = {k: v for k, v in row.items() if k != "id"}
        if route.get("iotedge_operation") or route.get("method_name"):
            route["iotedge"] = {
                "operation": route.pop("iotedge_operation") or "direct_method",
                "method_name": route.pop("method_name"),
            }
        else:
            route.pop("iotedge_operation", None)
            route.pop("method_name", None)
            route["iotedge"] = None
        routes.append(route)

    return ExtensionDetail(
        name=ext["name"],
        description=ext["description"],
        schema_version=ext["schema_version"],
        upstreams=upstreams,
        actions=actions,
        routes=routes,
        enabled=ext["enabled"],
    )


async def register_extension(
    extension_repo: ExtensionRepository,
    registration: ExtensionRegistration,
) -> ExtensionDetail:
    """Persists a brand-new extension with `enabled=false`. No routes are mounted.

    Schema fetches run before the write. The insert itself is one database
    transaction (`extension_repo.atomic`): a failure rolls the extension, its
    actions, and its routes back together. The IoT Edge canary probe stays
    outside that transaction; it only logs a warning and must not undo a save.
    """
    _validate_manifest_cross_references(registration)
    upstreams, routes = await _prepare_persistence(registration)

    try:
        async with extension_repo.atomic():
            if await extension_repo.extension_exists(registration.name):
                raise APIError(f"Extension '{registration.name}' is already registered", 409)
            await _validate_action_ownership(extension_repo, registration)
            await extension_repo.create_extension(registration.name, registration.description, registration.schema_version)
            await _enroll_actions(extension_repo, registration)
            await extension_repo.replace_upstreams_and_routes(registration.name, upstreams, routes)
    except ExtensionActionConflictError as exc:
        raise APIError(str(exc), 409) from exc

    await _probe_iotedge_upstreams(registration)
    return await _build_detail(extension_repo, registration.name)


async def replace_extension(
    extension_repo: ExtensionRepository,
    name: str,
    registration: ExtensionRegistration,
) -> ExtensionDetail:
    """Replaces `name`'s manifest in one database transaction. Never changes `enabled`.

    Refuses (409) to drop an action that is still granted to a role that check
    runs before any write. Live route remount, if the extension is enabled, is
    the router's job and happens after this commit. A remount failure cannot roll
    the database back; the in-memory routes are not part of the transaction.
    """
    if registration.name != name:
        raise APIError("Cannot change an extension's name via PUT replace", 400)

    existing = await extension_repo.get_extension_row(name)
    if existing is None:
        raise APIError(f"Extension '{name}' not found", 404)

    _validate_manifest_cross_references(registration)
    old_actions = set(await extension_repo.list_extension_actions(name))
    new_actions = {action.name for action in registration.actions}
    for removed in old_actions - new_actions:
        if await extension_repo.is_action_granted_to_any_role(removed):
            raise APIError(
                f"Action '{removed}' is still granted to a role. Revoke it before removing it from the manifest",
                409,
            )

    await extension_repo.release_connection()
    upstreams, routes = await _prepare_persistence(registration)
    try:
        async with extension_repo.atomic():
            await _validate_action_ownership(extension_repo, registration, current_extension=name)
            await extension_repo.set_description(name, registration.description)
            await extension_repo.set_schema_version(name, registration.schema_version)
            # A route's required_action FK depends on the action row already existing.
            await extension_repo.clear_extension_actions(name)
            await _enroll_actions(extension_repo, registration)
            await extension_repo.replace_upstreams_and_routes(name, upstreams, routes)
            await extension_repo.delete_orphaned_actions(list(old_actions - new_actions))
    except ExtensionActionConflictError as exc:
        raise APIError(str(exc), 409) from exc

    await _probe_iotedge_upstreams(registration)
    return await _build_detail(extension_repo, name)


async def get_extension(extension_repo: ExtensionRepository, name: str) -> ExtensionDetail:
    return await _build_detail(extension_repo, name)


async def list_extensions(extension_repo: ExtensionRepository) -> List[ExtensionDetail]:
    rows = await extension_repo.list_extensions_rows()
    return [await _build_detail(extension_repo, row["name"]) for row in rows]


async def check_extension_health(extension_repo: ExtensionRepository, name: str) -> ExtensionHealthCheckResult:
    """Runs a fresh health check of every one of `name`'s upstreams and persists the
    result, the sole trigger for a check (a plain `GET` only ever returns whatever was
    last persisted here). Upstreams are checked concurrently, so this call's latency is
    bounded by the single slowest upstream check, not their sum. Returns only the
    per-upstream health fields, not the full manifest (`ExtensionDetail`), this
    endpoint's job is reporting health, not re-describing routes/actions/description."""
    if await extension_repo.get_extension_row(name) is None:
        raise APIError(f"Extension '{name}' not found", 404)

    upstreams_rows = await extension_repo.list_upstreams(name)
    await extension_repo.release_connection()
    if upstreams_rows:
        checked_at = datetime.now(timezone.utc)
        results = await asyncio.gather(*(health.check_upstream_health(row) for row in upstreams_rows))
        for row, (status, detail) in zip(upstreams_rows, results):
            await extension_repo.record_upstream_health(row["id"], status, detail, checked_at)
            row["last_status"] = status
            row["last_detail"] = detail
            row["last_checked_at"] = checked_at

    return ExtensionHealthCheckResult(
        name=name,
        upstreams={
            row["key"]: {
                "last_checked_at": row.get("last_checked_at"),
                "last_status": row["last_status"],
                "last_detail": row.get("last_detail"),
            }
            for row in upstreams_rows
        },
    )


async def deregister_extension(
    extension_repo: ExtensionRepository,
    role_repo: RoleRepository,
    name: str,
) -> None:
    """Removes the extension. Unlike PUT, DELETE *does* strip the extension's
    actions from every role that holds them, then deletes leftover Action rows.
    PUT is an in-place edit of a still-live catalog entry (don't silently revoke);
    DELETE is "this extension is gone" (the actions it introduced go with it).
    All three steps commit together or not at all."""
    existing = await extension_repo.get_extension_row(name)
    if existing is None:
        raise APIError(f"Extension '{name}' not found", 404)

    async with extension_repo.atomic():
        action_names = await extension_repo.list_extension_actions(name)
        await _strip_actions_from_all_roles(role_repo, action_names)
        await extension_repo.delete_extension(name)  # cascades upstreams/routes/actions
        await extension_repo.delete_orphaned_actions(action_names)


async def enable_extension(extension_repo: ExtensionRepository, name: str) -> ExtensionDetail:
    existing = await extension_repo.get_extension_row(name)
    if existing is None:
        raise APIError(f"Extension '{name}' not found", 404)

    await extension_repo.set_enabled(name, True)
    return await _build_detail(extension_repo, name)


async def disable_extension(extension_repo: ExtensionRepository, name: str) -> ExtensionDetail:
    existing = await extension_repo.get_extension_row(name)
    if existing is None:
        raise APIError(f"Extension '{name}' not found", 404)

    await extension_repo.set_enabled(name, False)
    return await _build_detail(extension_repo, name)


async def rotate_internal_key(extension_repo: ExtensionRepository, name: str) -> str:
    """Generates a fresh internal key, immediately invalidating the previous one, single
    active key, no dual-key grace period. Returns the raw key; only ever visible here,
    never re-readable via GET."""
    existing = await extension_repo.get_extension_row(name)
    if existing is None:
        raise APIError(f"Extension '{name}' not found", 404)

    raw_key = generate_key()
    await extension_repo.set_internal_key_hash(name, hash_key(raw_key))
    return raw_key
