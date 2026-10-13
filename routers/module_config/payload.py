"""Literal JSON for the core write helper, without provider-model normalization."""

import json
import math
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse


class ModuleConfigPayload:
    """Pre-encodes literal JSON before writes; the helper's exclude_none flag never prunes it."""

    def __init__(self, payload: Any) -> None:
        self._json = _encode_module_json(payload)

    def model_dump_json(self, *, exclude_none: bool = False) -> str:
        return self._json


def _encode_module_json(payload: Any) -> str:
    """Traverse containers iteratively so encoding adds no depth limit to parsed JSON.
    Scalar encoding uses standard ASCII escapes, including accepted lone surrogates."""
    chunks = []
    pending = [(payload, False)]
    while pending:
        value, is_token = pending.pop()
        if is_token:
            chunks.append(value)
        elif isinstance(value, ModuleConfigPayload):
            chunks.append(value.model_dump_json())
        elif isinstance(value, dict):
            chunks.append("{")
            pending.append(("}", True))
            for index, (key, item) in reversed(list(enumerate(value.items()))):
                pending.extend([(item, False), (":", True), (key, False)])
                if index:
                    pending.append((",", True))
        elif isinstance(value, (list, tuple)):
            chunks.append("[")
            pending.append(("]", True))
            for index in range(len(value) - 1, -1, -1):
                pending.append((value[index], False))
                if index:
                    pending.append((",", True))
        else:
            chunks.append(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return "".join(chunks)


class ModuleJSONResponse(JSONResponse):
    """Module-only transport bypasses ORM-key pruning and UTF-8 surrogate failures."""

    def render(self, content: Any) -> bytes:
        return _encode_module_json(content).encode("ascii")


def parse_module_json(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError("Non-finite JSON number")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number")
        return number

    try:
        return json.loads(raw, parse_constant=reject_constant, parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(status_code=400, detail="Request body must be valid finite JSON")


_RULE_MESSAGES = {
    "type": "must match the declared type",
    "required": "must include the required properties",
    "enum": "must be one of the allowed values",
    "const": "must match the required value",
    "additionalProperties": "contains properties that are not allowed",
    "unevaluatedProperties": "contains properties that are not allowed",
    "minimum": "must meet the minimum",
    "maximum": "must meet the maximum",
    "exclusiveMinimum": "must exceed the minimum",
    "exclusiveMaximum": "must be below the maximum",
    "minLength": "is shorter than allowed",
    "maxLength": "is longer than allowed",
    "pattern": "must match the declared pattern",
    "minItems": "has fewer items than allowed",
    "maxItems": "has more items than allowed",
    "uniqueItems": "must contain unique items",
    "anyOf": "must match an allowed shape",
    "oneOf": "must match exactly one allowed shape",
}


def invalid_module_payload(errors: list[dict]) -> JSONResponse:
    """Never relay jsonschema messages: many rules include instance values or schemas."""
    bounded = []
    for error in errors[:20]:
        rule = error.get("rule")
        bounded.append({
            "loc": [part if isinstance(part, int) else str(part)[:128] for part in error.get("loc", [])[:16]],
            "rule": rule if rule in _RULE_MESSAGES else "schema",
            "msg": _RULE_MESSAGES.get(rule, "must satisfy the declared constraint"),
        })
    return ModuleJSONResponse(status_code=422, content={
        "message": "Module configuration does not match its schema", "errors": bounded,
    })
