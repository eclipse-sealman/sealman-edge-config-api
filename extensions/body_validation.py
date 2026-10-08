"""Request-time JSON Schema validation for `declared`-mode extension route bodies.

Keeps literal JSON Schema (via the reference `jsonschema` library) instead of
compiling it to a Pydantic model: one compiled validator per route, cached and reused
across requests; every failing field reported at once, not just the first; and `$ref`
resolution failing closed (no network call ever happens during validation) rather
than silently succeeding or hanging.
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry

# One JSON Schema validator per route, built once (at first use) and reused for every
# subsequent request against that route. Never rebuilt inside the request handler.
_validator_cache: Dict[str, Draft202012Validator] = {}

# No `retrieve` callback: an external `$ref` in a registered schema raises instead of
# making a network call during validation, an SSRF-shaped risk closed by construction.
_NO_REMOTE_REFS = Registry()


def check_schema(schema: dict) -> None:
    """Raises jsonschema.exceptions.SchemaError if `schema` isn't itself a valid
    Draft 2020-12 JSON Schema. Called at registration time, before anything is cached."""
    Draft202012Validator.check_schema(schema)


def _curate(error: ValidationError) -> str:
    """Builds the human-readable message from `error.validator`/`error.validator_value`
    rather than trusting `error.message` verbatim, also where an oversized `enum`
    value list gets truncated instead of dumped whole."""
    if error.validator == "enum":
        values = list(error.validator_value)
        if len(values) > 10:
            values = values[:10] + ["...(truncated)"]
        return f"must be one of {values}"
    if error.validator == "required":
        missing = list(error.validator_value)
        return f"missing required propert{'y' if len(missing) == 1 else 'ies'}: {missing}"
    if error.validator == "type":
        return f"must be of type {error.validator_value}"
    return error.message


def _errors_to_dicts(errors: List[ValidationError]) -> List[Dict[str, Any]]:
    return [{"loc": list(error.path), "rule": error.validator, "msg": _curate(error)} for error in errors]


def validate_once(schema: dict, payload: Any) -> List[Dict[str, Any]]:
    """One-off validation with no caching for registration-time checks (e.g. an
    `example` against its own route's `body`) that run once, not per request."""
    validator = Draft202012Validator(schema, registry=_NO_REMOTE_REFS)
    return _errors_to_dicts(list(validator.iter_errors(payload)))


def get_validator(route_id: str, schema: dict) -> Draft202012Validator:
    validator = _validator_cache.get(route_id)
    if validator is None:
        validator = Draft202012Validator(schema, registry=_NO_REMOTE_REFS)
        _validator_cache[route_id] = validator
    return validator


def invalidate(route_id: str) -> None:
    """Drops a route's cached validator call whenever a route is unmounted, since a
    re-mount (enable, or a fresh register/replace) always gets a brand-new route id and
    a stale cache entry would otherwise just leak, never being read again."""
    _validator_cache.pop(route_id, None)


def validate_payload(route_id: str, schema: dict, payload: Any) -> List[Dict[str, Any]]:
    """Validates `payload` against `schema` via the route's cached validator, collecting
    every failing field instead of raising on the first one."""
    validator = get_validator(route_id, schema)
    return _errors_to_dicts(list(validator.iter_errors(payload)))


MAX_BUFFERED_BODY_BYTES = 1024 * 1024


async def read_limited_body(request: Request, limit: int = MAX_BUFFERED_BODY_BYTES) -> bytes:
    """Reads the whole request body into memory, failing with 413 as soon as it would
    exceed `limit` bytes. Only for routes that must buffer; the http proxy streams."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"Request body exceeds {limit} bytes")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"Request body exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_json(raw: bytes) -> Optional[Any]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")
