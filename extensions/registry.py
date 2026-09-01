"""Business logic for the dynamic API extension system: validates a
registration manifest, enrolls the extension's actions into the existing RBAC
catalog (``actions`` / ``roles`` tables, via :mod:`authorization`), persists
the extension + its routes, and issues the internal / per-device keys.

Route dispatch/mounting lives in :mod:`extensions.runtime`; this module only
owns persistence + validation, mirroring how ``db/repos`` + router modules are
layered elsewhere in this codebase.
"""
import re
from typing import Any, Optional

import jsonschema
from fastapi import HTTPException

from db.repos.extension import ExtensionRepository
from db.repos.role import RoleRepository

from .schemas import ExtensionRegistration
from .security import generate_key, hash_key, keys_match

_PATH_PARAM_RE = re.compile(r"{([^}]+)}")


async def register_extension(
    extension_repo: ExtensionRepository,
    role_repo: RoleRepository,
    payload: ExtensionRegistration,
) -> dict[str, Any]:
    """Persist the extension, enroll its actions and grant them to the
    requested roles. Returns the added routes + issued internal key (if any).
    Raises ``ValueError`` on validation/conflict errors (mapped to 409/422 by
    the caller)."""
    if await extension_repo.get_extension(payload.name) is not None:
        raise ValueError(f"Extension '{payload.name}' is already registered")

    for role_name in payload.grant_to_roles:
        if await role_repo.get_by_name(role_name) is None:
            raise ValueError(f"Unknown role: '{role_name}'")

    if not payload.upstreams:
        raise ValueError("At least one upstream must be defined")
    for key, up in payload.upstreams.items():
        if up.type == "http" and not up.base_url:
            raise ValueError(f"Upstream '{key}' is type 'http' but has no 'base_url'")
        if up.type == "iotedge" and not up.module_name:
            raise ValueError(f"Upstream '{key}' is type 'iotedge' but has no 'module_name'")

    resolved: list[tuple[Any, str]] = []
    for r in payload.routes:
        up = payload.upstreams.get(r.upstream)
        if up is None:
            raise ValueError(f"Route '{r.path}' references unknown upstream '{r.upstream}'")
        transport = up.type

        if transport == "http":
            default_method = "GET"
        elif r.iotedge and r.iotedge.operation == "twin_read":
            default_method = "GET"
        else:
            default_method = "POST"
        method = (r.method or default_method).upper()

        path_params = set(_PATH_PARAM_RE.findall(r.path))
        if r.visibility == "public":
            if not r.required_action:
                raise ValueError(f"public route '{method} {r.path}' requires 'required_action'")
            if r.scoped and r.scope_in == "path" and r.scope_param not in path_params:
                raise ValueError(
                    f"Route '{method} {r.path}' is path-scoped on '{r.scope_param}' "
                    f"but the path has no '{{{r.scope_param}}}' parameter"
                )

        if r.visibility == "device" and transport != "http":
            raise ValueError(
                f"'device' route '{method} {r.path}' must target an 'http' upstream "
                "(edge module -> micro-service)"
            )

        if transport == "http":
            if not r.upstream_path:
                raise ValueError(f"http route '{method} {r.path}' requires 'upstream_path'")
            if r.iotedge:
                raise ValueError(f"http route '{method} {r.path}' must not set 'iotedge'")
            if r.body and method in ("GET", "DELETE"):
                raise ValueError(
                    f"Route '{method} {r.path}' declares a body but '{method}' requests carry none"
                )
        else:
            if not r.iotedge:
                raise ValueError(
                    f"iotedge route '{r.path}' requires an 'iotedge' block "
                    "(operation + method_name for 'direct_method')"
                )

        if r.body is not None:
            if not isinstance(r.body, dict):
                raise ValueError(f"Route '{method} {r.path}' body must be a JSON Schema object")
            try:
                jsonschema.Draft202012Validator.check_schema(r.body)
            except jsonschema.SchemaError as ex:
                raise ValueError(
                    f"Route '{method} {r.path}' has an invalid body JSON Schema: {ex.message}"
                )

        resolved.append((r, method))

    has_internal = any(r.visibility == "internal" for r in payload.routes)
    internal_key = generate_key() if has_internal else None

    await extension_repo.create_extension(
        name=payload.name,
        upstreams={k: v.model_dump() for k, v in payload.upstreams.items()},
        description=payload.description,
        internal_key_hash=hash_key(internal_key) if internal_key else None,
    )

    for spec in payload.actions:
        await extension_repo.ensure_action(spec.name, spec.description, is_global=False)
        await extension_repo.record_extension_action(payload.name, spec.name)

    if payload.actions and payload.grant_to_roles:
        action_names = [a.name for a in payload.actions]
        for role_name in payload.grant_to_roles:
            role = await role_repo.get_by_name(role_name)
            new_actions = [a for a in action_names if a not in (role.get("actions") or [])]
            if new_actions:
                await role_repo.add_actions_to_role(role["id"], new_actions)

    added_routes = []
    for r, method in resolved:
        route_dict = {
            "upstream": r.upstream,
            "path": r.path,
            "method": method,
            "upstream_path": r.upstream_path,
            "method_name": r.iotedge.method_name if r.iotedge else None,
            "iotedge_operation": r.iotedge.operation if r.iotedge else "direct_method",
            "required_action": r.required_action,
            "visibility": r.visibility,
            "scoped": r.scoped,
            "scope_param": r.scope_param,
            "scope_in": r.scope_in,
            "query_params": [q.model_dump() for q in r.query_params],
            "body": r.body,
            "summary": r.summary,
            "description": r.description,
        }
        await extension_repo.add_route(payload.name, route_dict)
        added_routes.append(route_dict)

    return {
        "routes": added_routes,
        "actions_added": [a.name for a in payload.actions],
        "granted_to_roles": payload.grant_to_roles,
        "internal_key": internal_key,
    }


async def deregister_extension(
    extension_repo: ExtensionRepository,
    role_repo: RoleRepository,
    name: str,
) -> dict[str, Any]:
    """Remove the extension (routes/device-keys cascade), detach its actions
    from every role that has them, and delete actions no longer used by any
    other extension. Raises ``ValueError`` if the extension doesn't exist."""
    actions = await extension_repo.list_extension_actions(name)

    deleted = await extension_repo.delete_extension(name)
    if not deleted:
        raise ValueError(f"Extension '{name}' not found")

    if actions:
        for role in await role_repo.list_roles():
            for action_name in actions:
                if action_name in (role.get("actions") or []):
                    await role_repo.remove_action_from_role(role["id"], action_name)
        await extension_repo.delete_orphaned_actions(actions)

    return {"actions_removed": actions}


async def issue_device_key(
    extension_repo: ExtensionRepository, extension_name: str, device_id: str, module_id: Optional[str]
) -> str:
    if await extension_repo.get_extension(extension_name) is None:
        raise HTTPException(status_code=404, detail=f"Extension '{extension_name}' not found")
    raw = generate_key()
    await extension_repo.upsert_device_key(extension_name, device_id, module_id, hash_key(raw))
    return raw


async def verify_internal_key(extension_repo: ExtensionRepository, extension_name: str, presented: str) -> None:
    """Validate the ``X-Internal-Key`` of an internal (service-to-service) call.
    Namespace-locked: the key must match exactly the extension that owns the route."""
    ext = await extension_repo.get_extension(extension_name)
    if not ext or not ext.get("internal_key_hash"):
        raise HTTPException(status_code=403, detail="This extension has no internal key configured.")
    if not keys_match(presented or "", ext["internal_key_hash"]):
        raise HTTPException(status_code=401, detail="Invalid or missing internal API key.")


async def verify_device_key(extension_repo: ExtensionRepository, extension_name: str, presented: str) -> str:
    """Validate the ``X-Device-Key`` of a device (edge module) calling in.
    Returns the resolved ``device_id`` to forward to the micro-service."""
    if not presented:
        raise HTTPException(status_code=401, detail="Missing device key (X-Device-Key).")
    device_id = await extension_repo.resolve_device_key(extension_name, hash_key(presented))
    if not device_id:
        raise HTTPException(status_code=401, detail="Invalid device key for this extension.")
    return device_id
