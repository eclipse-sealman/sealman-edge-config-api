"""Helpers to enrich proxied HTTP routes from an upstream service's OpenAPI specification."""
import copy
import logging
from urllib.parse import urljoin
from typing import Any, Optional

import httpx

logger = logging.getLogger("EdgeConfigAPI")

# Cache successfully fetched specs for the lifetime of the process.
_OPENAPI_CACHE: dict[str, dict[str, Any]] = {}


def clear_openapi_cache(base_url: Optional[str] = None) -> None:
    if base_url is None:
        _OPENAPI_CACHE.clear()
        return
    _OPENAPI_CACHE.pop(base_url, None)


def _openapi_url(base_url: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", "openapi.json")


def _normalize_path(path: str) -> str:
    path = "/" + (path or "").lstrip("/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path


def _resolve_local_refs(node: Any, components: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """Inline local '#/components/schemas/*' refs so they can be embedded
    directly into another FastAPI app's OpenAPI operation."""
    if isinstance(node, list):
        return [_resolve_local_refs(item, components, seen) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        schema_name = ref.rsplit("/", 1)[-1]
        target = components.get(schema_name)
        if not isinstance(target, dict) or schema_name in seen:
            return copy.deepcopy(node)
        merged = copy.deepcopy(target)
        extras = {k: v for k, v in node.items() if k != "$ref"}
        if extras:
            merged.update(_resolve_local_refs(extras, components, seen | {schema_name}))
        return _resolve_local_refs(merged, components, seen | {schema_name})

    return {k: _resolve_local_refs(v, components, seen) for k, v in node.items()}


def _fetch_openapi_spec(base_url: str) -> Optional[dict[str, Any]]:
    if not base_url:
        return None
    cached = _OPENAPI_CACHE.get(base_url)
    if cached is not None:
        return cached
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(_openapi_url(base_url))
            resp.raise_for_status()
            spec = resp.json()
    except Exception as exc:
        logger.debug(f"Failed to fetch upstream OpenAPI from {base_url}: {exc}")
        return None

    if not isinstance(spec, dict):
        logger.debug(f"Ignoring non-object OpenAPI document from {base_url}")
        return None

    _OPENAPI_CACHE[base_url] = spec
    return spec


def infer_http_route_docs(base_url: str, upstream_path: str, method: str) -> Optional[dict[str, Any]]:
    """Best-effort extraction of request-body docs from an upstream HTTP
    service's OpenAPI document.

    Returns a dict with any of: 'body', 'body_required', 'summary',
    'description'. Returns None when the upstream spec is unavailable or
    the operation cannot be found.
    """
    spec = _fetch_openapi_spec(base_url)
    if not spec:
        return None

    path_item = (spec.get("paths") or {}).get(_normalize_path(upstream_path)) or {}
    operation = path_item.get((method or "").lower()) or {}
    if not isinstance(operation, dict):
        return None

    out: dict[str, Any] = {}
    if operation.get("summary"):
        out["summary"] = operation["summary"]
    if operation.get("description"):
        out["description"] = operation["description"]

    request_body = operation.get("requestBody") or {}
    if not isinstance(request_body, dict):
        return out or None

    content = request_body.get("content") or {}
    media = content.get("application/json")
    if not isinstance(media, dict):
        for media_type, candidate in content.items():
            if "json" in media_type and isinstance(candidate, dict):
                media = candidate
                break
    if not isinstance(media, dict):
        return out or None

    schema = media.get("schema")
    if not isinstance(schema, dict):
        return out or None

    components = (spec.get("components") or {}).get("schemas") or {}
    out["body"] = _resolve_local_refs(schema, components)
    out["body_required"] = bool(request_body.get("required", False))
    return out
