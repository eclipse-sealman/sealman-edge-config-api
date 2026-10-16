"""Caller-filtered capabilities from persisted routes; never contacts a runtime."""

import copy
import logging

from fastapi import HTTPException
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from authorization.abac_permission_check import evaluate_permission
from db.repos.device import DeviceRepository
from db.repos.extension import ExtensionRepository
from db.repos.user import UserRepository

from . import signature
from .schemas import DirectMethodDiscovery, DirectMethodInvocation, DiscoveredDirectMethod


logger = logging.getLogger("EdgeConfigAPI")


def _require_self_contained_body(name: str, route: dict) -> None:
    schema = route.get("body")
    if schema is None:
        return
    try:
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        # No retrieve callback: every reference must resolve inside this descriptor.
        resolver = Registry().with_resource("", resource).crawl().resolver("").in_subresource(resource)
        pending = [(resource, resolver)]
        visited = set()
        while pending:
            current, current_resolver = pending.pop()
            if id(current.contents) in visited:
                continue
            visited.add(id(current.contents))
            if isinstance(current.contents, dict):
                for keyword in ("$ref", "$dynamicRef"):
                    if keyword in current.contents:
                        resolved = current_resolver.lookup(current.contents[keyword])
                        target = Resource.from_contents(resolved.contents, default_specification=DRAFT202012)
                        pending.append((target, resolved.resolver))
            pending.extend(
                (child, current_resolver.in_subresource(child)) for child in current.subresources()
            )
    except Exception:
        # Neither a reference URI nor the schema/body can enter errors or logs.
        logger.error("Direct-method body schema misconfigured: extension '%s', route '%s'", name, route["id"])
        raise HTTPException(status_code=500, detail="This route's body schema is misconfigured") from None


async def list_direct_methods(
    name: str,
    device_id: str,
    auth_context: dict,
    extension_repo: ExtensionRepository,
    user_repo: UserRepository,
    device_repo: DeviceRepository,
) -> DirectMethodDiscovery:
    user_id = auth_context.get("oid") or auth_context.get("sub")
    if not user_id:
        raise HTTPException(403, "User identifier missing from token")
    user_data = await user_repo.get_teams_with_roles_and_scopes(user_id)
    if user_data is None:
        raise HTTPException(403, "User not found")
    device_meta = await device_repo.get_device_meta_raw(device_id)
    if device_meta is None:
        raise HTTPException(404, f"Device '{device_id}' not found")
    extension = await extension_repo.get_extension_row(name)
    if extension is None:
        raise HTTPException(404, f"Extension '{name}' not found")

    result = DirectMethodDiscovery(
        extension=name, device_id=device_id, enabled=bool(extension.get("enabled")), methods=[]
    )
    if not result.enabled:
        return result

    upstreams = {row["key"]: row for row in await extension_repo.list_upstreams(name)}
    grouped: dict[tuple[str, str], list[DirectMethodInvocation]] = {}
    for route in await extension_repo.list_routes(name):
        upstream = upstreams.get(route["upstream"])
        if (
            (route.get("visibility") or "public") != "public"
            or upstream is None
            or upstream.get("type") != "iotedge"
            or route.get("iotedge_operation") != "direct_method"
            or not route.get("method_name")
            or not upstream.get("module_name")
        ):
            continue
        action = route.get("required_action")
        scoped = bool(route.get("scoped"))
        # Public invocation routes without an action have no ABAC dependency.
        if action and not evaluate_permission(action, user_data, device_meta if scoped else None):
            continue

        _require_self_contained_body(name, route)
        # Use the same implicit target parameters as the mounted invocation signature.
        path_params = set(signature._PATH_PARAM_RE.findall(route["path"]))
        query_params = {}
        for spec in list(route.get("query_params") or []) + signature._implicit_query_params(route):
            if spec["name"] not in path_params and spec["name"] != "request":
                # build_signature retains the first declaration of a parameter.
                query_params.setdefault(spec["name"], spec)
        invocation = DirectMethodInvocation(
            path=route["path"],
            http_method=route.get("method") or "GET",
            summary=route.get("summary") or f"{name}: {route.get('method') or 'GET'} {route['path']}",
            description=route.get("description"),
            query_params=[query_params[key] for key in sorted(query_params)],
            scoped=scoped,
            scope_param=(route.get("scope_param") or "device_id") if scoped else None,
            scope_in=(route.get("scope_in") or "query") if scoped else None,
            request_schema=copy.deepcopy(route.get("body")),
        )
        grouped.setdefault((upstream["module_name"], route["method_name"]), []).append(invocation)

    result.methods = [
        DiscoveredDirectMethod(
            module_id=module_id,
            method_name=method_name,
            invocations=sorted(invocations, key=lambda item: (item.path, item.http_method)),
        )
        for (module_id, method_name), invocations in sorted(grouped.items())
    ]
    return result
