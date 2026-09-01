"""Reverse-proxy helper used to forward authorized requests to an extension's
'http' upstream micro-service. Authorization already happened before this is
called (ABAC for public routes, X-Internal-Key / X-Device-Key for the
internal / device channels).
"""
import re

from fastapi import Request, Response

from async_requests import client as _http_client

# Hop-by-hop / host headers we must not forward verbatim.
_STRIP_HEADERS = {"host", "content-length", "connection"}

_PLACEHOLDER_RE = re.compile(r"{([^}]+)}")


async def proxy_request(
    base_url: str,
    upstream_path: str,
    method: str,
    request: Request,
    path_vars: dict,
    extra_headers: dict = None,
) -> Response:
    """Forward the incoming request to ``base_url + upstream_path`` and relay
    the upstream response back to the caller.

    * ``{placeholder}`` tokens in ``upstream_path`` are filled from ``path_vars``.
    * Path variables not consumed as a placeholder are appended to the upstream
      query string.
    * The original query string and request body are forwarded as-is, streamed
      (so large uploads are not fully buffered in memory).
    * ``extra_headers`` are added to the forwarded request (e.g. ``X-Device-Id``).
    """
    used = set(_PLACEHOLDER_RE.findall(upstream_path))
    resolved_path = upstream_path.format(**{k: v for k, v in path_vars.items() if k in used})
    target = base_url.rstrip("/") + resolved_path

    params = dict(request.query_params)
    for key, value in path_vars.items():
        if key not in used and key not in params:
            params[key] = value

    forward_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _STRIP_HEADERS
    }
    forward_headers["X-Forwarded-By"] = "sealman-edge-config-api"
    if extra_headers:
        forward_headers.update(extra_headers)

    try:
        upstream = await _http_client.request(
            method,
            target,
            params=params,
            content=request.stream(),
            headers=forward_headers,
            timeout=60,
        )
    except Exception as exc:  # upstream unreachable / timed out
        return Response(
            content=(
                "{"
                f'"message":"Upstream error calling {target}: {exc}"'
                "}"
            ),
            status_code=502,
            media_type="application/json",
        )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
