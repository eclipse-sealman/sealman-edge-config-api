"""Builds the `inspect.Signature` FastAPI binds a mounted extension route's dispatch
callable to, path and query parameters only.
"""

import inspect
import keyword
import re
from typing import Any, Dict, List, Optional, Type

from fastapi import Path, Query, Request

_PATH_PARAM_RE = re.compile(r"{([^}]+)}")

_QUERY_PARAM_TYPES: Dict[str, Type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _implicit_query_params(route: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Query params the runtime needs even if the manifest omitted them.

    * `scoped` + `scope_in=query`: the ABAC/iotedge device identifier.
    * unscoped `iotedge` routes: `device_id`, which dispatch reads as the IoT Hub target.
    Path-scoped identifiers are already bound as path params and are not repeated.
    """
    extras: List[Dict[str, Any]] = []
    path_params = set(_PATH_PARAM_RE.findall(route.get("path") or ""))
    declared = {spec.get("name") for spec in (route.get("query_params") or [])}

    def _ensure(name: str, description: str) -> None:
        if name in path_params or name in declared or any(e["name"] == name for e in extras):
            return
        extras.append({"name": name, "type": "string", "required": True, "description": description})

    if route.get("scoped") and (route.get("scope_in") or "query") == "query":
        _ensure(
            route.get("scope_param") or "device_id",
            "Device identifier (`devices.device_id`) used for ABAC scope and iotedge targeting.",
        )
    elif (route.get("iotedge") or route.get("iotedge_operation")) and not route.get("scoped"):
        _ensure("device_id", "Target IoT Hub device id (`devices.device_id`) the module runs on.")
    return extras


def build_signature(route: Dict[str, Any]) -> inspect.Signature:
    """Returns `request: Request` (the dispatcher only ever reads the raw `Request`
    itself; these bound values exist purely so FastAPI validates/coerces/documents them
    like it would a real route) plus one keyword-only parameter per `{name}` placeholder
    in `route['path']` and one per `route['query_params']` entry, plus implicit device-id
    query params iotedge/ABAC need when they are not already in the path or manifest."""
    parameters = [inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request)]
    used_names = {"request"}
    used_python_names = {"request"}

    for name in _PATH_PARAM_RE.findall(route.get("path") or ""):
        if name in used_names:
            continue
        used_names.add(name)
        used_python_names.add(name)
        parameters.append(
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=str, default=Path(...))
        )

    for spec in list(route.get("query_params") or []) + _implicit_query_params(route):
        name = spec["name"]
        if name in used_names:
            continue
        used_names.add(name)
        python_name = _python_name(name, used_python_names)
        used_python_names.add(python_name)
        py_type = _QUERY_PARAM_TYPES.get(spec.get("type") or "string", str)
        required = spec.get("required", True)
        description = spec.get("description")
        if required:
            annotation: Any = py_type
            default = Query(..., alias=name, description=description)
        else:
            annotation = Optional[py_type]
            default = Query(None, alias=name, description=description)
        parameters.append(
            inspect.Parameter(python_name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default)
        )

    return inspect.Signature(parameters)


def _python_name(query_name: str, taken: set) -> str:
    """Query names like `page-size` are not identifiers; the real name travels as the alias."""
    if query_name.isidentifier() and not keyword.iskeyword(query_name) and query_name not in taken:
        return query_name
    index = 0
    while f"query_param_{index}" in taken:
        index += 1
    return f"query_param_{index}"
