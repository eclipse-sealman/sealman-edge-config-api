"""Reverse-proxy dispatch for `http`-type upstreams: a true streaming pass-through,
built on httpx's own documented streaming-proxy pattern so neither direction is ever
fully buffered in memory, for any content type or size.

`request.stream()` in / `resp.aiter_raw()` out: `aiter_raw()` specifically (not
`aiter_bytes()`/`aiter_text()`) skips httpx's automatic content-decoding, so
gzip/br/zstd-compressed bodies, multipart/form-data and text/event-stream (SSE) all
pass through byte-identical and progressively. Dispatch never needs to
understand the upstream's content type to relay it correctly.
"""

import logging
import re
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response, StreamingResponse

from constants import ALLOW_INSECURE_HTTPS

logger = logging.getLogger("EdgeConfigAPI")

_PLACEHOLDER_RE = re.compile(r"{([^}]+)}")

_REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "accept-encoding",
    "content-type",
    "content-encoding",
    "if-match",
    "if-none-match",
    "range",
    "idempotency-key",
    "traceparent",
    "tracestate",
    "x-request-id",
}
_RESPONSE_HEADER_ALLOWLIST = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-encoding",
    "content-language",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
    "location",
    "retry-after",
    "vary",
}
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_REQUEST_HEADER_DENYLIST = _HOP_BY_HOP | {
    "authorization",
    "cookie",
    "host",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-internal-key",
}
_RESPONSE_HEADER_DENYLIST = _HOP_BY_HOP | {"date", "server", "set-cookie"}

# One shared client (connection pooling), same pattern as async_requests.py's module-level client.
# A read timeout bounds the gap between chunks, not the whole response.
_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)
_client = httpx.AsyncClient(verify=not ALLOW_INSECURE_HTTPS, timeout=_TIMEOUT)


def _resolve_upstream_path(upstream_path: str, request: Request) -> str:
    """Fills `{name}` placeholders in `upstream_path` from the mounted route's own,
    already-resolved path parameters, percent-encoding each value as one path segment.
    Registration guarantees every placeholder is supplied by the mounted path."""
    path_params = request.path_params

    def _substitute(match: re.Match) -> str:
        value = str(path_params[match.group(1)])
        # quote() leaves dots alone, and httpx would normalize these segments away.
        if value in (".", ".."):
            raise HTTPException(status_code=400, detail="Invalid path parameter value")
        return quote(value, safe="")

    return _PLACEHOLDER_RE.sub(_substitute, upstream_path)


def _connection_tokens(values: Iterable[str]) -> Set[str]:
    return {token.strip().lower() for value in values for token in value.split(",") if token.strip()}


def _request_headers(request: Request) -> List[Tuple[str, str]]:
    denied = _REQUEST_HEADER_DENYLIST | _connection_tokens(request.headers.getlist("connection"))
    headers = [
        (name, value)
        for name, value in request.headers.items()
        if name.lower() in _REQUEST_HEADER_ALLOWLIST and name.lower() not in denied
    ]
    _add_forwarding_headers(request, headers)
    return headers


def _add_forwarding_headers(request: Request, headers: List[Tuple[str, str]]) -> None:
    """Adds forwarding metadata derived only from this trusted proxy hop."""
    client_host = request.client.host if request.client else None
    if client_host:
        headers.append(("x-forwarded-for", client_host))
    original_host = request.headers.get("host")
    if original_host:
        headers.append(("x-forwarded-host", original_host))
    headers.append(("x-forwarded-proto", request.url.scheme))


def _response_headers(response: httpx.Response) -> List[Tuple[bytes, bytes]]:
    denied = _RESPONSE_HEADER_DENYLIST | _connection_tokens(response.headers.get_list("connection"))
    headers = []
    for name, value in response.headers.raw:
        normalized_name = name.decode("ascii").lower()
        if normalized_name in _RESPONSE_HEADER_ALLOWLIST and normalized_name not in denied:
            headers.append((normalized_name.encode("ascii"), value))
    return headers


def _streamed_body(request: Request, headers: List[Tuple[str, str]]) -> Optional[AsyncIterator[bytes]]:
    """Streams the body only if the request declares one. Keeping its Content-Length
    stops httpx from switching to chunked transfer encoding."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        headers.append(("content-length", content_length))
        return request.stream()
    if "transfer-encoding" in request.headers:
        return request.stream()
    return None


def _upstream_error(exc: httpx.RequestError) -> Tuple[int, str]:
    if isinstance(exc, httpx.PoolTimeout):
        return 503, "Upstream busy"
    if isinstance(exc, httpx.TimeoutException):
        return 504, "Upstream timed out"
    return 502, "Upstream unavailable"


async def dispatch(
    request: Request, upstream: Dict[str, Any], route: Dict[str, Any], body: Optional[bytes] = None
) -> Response:
    """Forwards the live request to `upstream['base_url'] + route['upstream_path']` and
    streams the response straight back. `body` is only set when the caller already had
    to buffer it (declared-mode validation); otherwise the request body is streamed."""
    upstream_path = _resolve_upstream_path(route["upstream_path"], request)
    target = upstream["base_url"].rstrip("/") + upstream_path

    headers = _request_headers(request)
    content = body if body is not None else _streamed_body(request, headers)
    req = _client.build_request(
        request.method,
        target,
        params=request.query_params.multi_items(),
        headers=headers,
        content=content,
    )
    try:
        resp = await _client.send(req, stream=True)
    except httpx.RequestError as exc:
        status_code, detail = _upstream_error(exc)
        logger.warning(
            f"Extension http upstream request failed: {request.method} {target} ({type(exc).__name__}: {exc})"
        )
        return JSONResponse({"detail": detail}, status_code=status_code)

    response = StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        background=BackgroundTask(resp.aclose),
    )
    response.raw_headers.extend(_response_headers(resp))
    return response


async def fetch_openapi_document(base_url: str) -> Optional[dict]:
    """`upstream_declared`/`unreachable_ref` support: fetches the extension's own
    `{base_url}/openapi.json`, the only origin ever queried (the upstream's own
    already-persisted `base_url`, `follow_redirects` never enabled). Returns None for an
    unreachable host, non-2xx, or a non-object body; never raises."""
    target = base_url.rstrip("/") + "/openapi.json"
    try:
        resp = await _client.get(target, timeout=10, follow_redirects=False)
        resp.raise_for_status()
        document = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info(f"Could not fetch OpenAPI document from {target}: {exc}")
        return None
    return document if isinstance(document, dict) else None


def body_schema_from_document(document: Optional[dict], upstream_path: str, method: str) -> Optional[dict]:
    """Returns a route's JSON request-body schema from a fetched OpenAPI document, or None
    (→ `unreachable_ref`) if the document is missing or does not declare one."""
    if document is None:
        return None
    try:
        operation = document["paths"][upstream_path][method.lower()]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        ref = schema.get("$ref") if isinstance(schema, dict) else None
        if ref and ref.startswith("#/components/schemas/"):
            schema = document["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        return schema
    except (KeyError, TypeError, AttributeError) as exc:
        logger.info(f"OpenAPI document declares no JSON body for {method} {upstream_path}: {exc}")
        return None

