"""
Structural JSON templating for module configurations.

A template is a JSON object whose string values may contain placeholders:

- ``${name}``          a declared variable (user input); type ``text``
                       is a string entered in a multi-line field
- ``${device.field}``  a read-only device metadata field (dotted path into ``device_data``)
- ``${endpoint.field}`` / ``${endpoint[slot].field}``
                       a field of an endpoint of the device, chosen per slot when the template is
                       rendered for a device (``${endpoint.x}`` is the slot ``endpoint``). ``ip`` and
                       ``name`` also resolve through the endpoint type's field roles.
- ``${endpoint.field.sub}`` / ``${endpoint[slot].field.sub}``
                       the ``sub`` entry of one row of the endpoint's list field ``field``; the row is
                       chosen per device when the template is rendered.
- ``${service[slot].field}``
                       a field of the service chosen (per device) for endpoint slot ``slot``; the body
                       must also use that endpoint slot. ``port`` also resolves through the service
                       type's field roles.
- ``$${``              a literal ``${``

A string that consists of exactly one placeholder keeps the value's JSON type, otherwise the
value is interpolated as text. An optional variable without value removes the
key (or list item) it stands in for. Keys are never substituted.

A value ``{"$ref": "<id>"}`` (the only key; stored form, people write ``<block name>``) inserts a copy of that block's
latest version. Each copy
is named after its nearest object key (``list[0]`` inside arrays) and its variables and endpoint
slots are keyed ``<copy>/<name>``; nested copies join with ``/``.

A value ``{"$list": ["<id>", ...]}`` (only in a template, not inside an array; optionally with
``"$label": "<item label>"`` naming its items) holds entries chosen per device, each a copy of one of the listed blocks. The list is named like a copy; an entry renders
like a copy with prefix ``<list>/<entry name>``, and the list renders to ``{entry name: block}``.

``$secret`` is reserved for a later version and rejected.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

VARIABLE_TYPES = ("string", "text", "number", "integer", "boolean", "enum")
RESERVED_KEYS = ("$secret",)
REF_KEY = "$ref"
REF_RE = re.compile(r"\d+")
LIST_KEY = "$list"
LABEL_KEY = "$label"
ENTRY_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
COPY_SEP = "/"
DEVICE_PREFIX = "device."
DEFAULT_SLOT = "endpoint"

_TOKEN_RE = re.compile(r"\$\$\{|\$\{([^{}]*)\}")
_WHOLE_RE = re.compile(r"\$\{([^{}]*)\}")
_VARIABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEVICE_PATH_RE = re.compile(r"device(\.[A-Za-z0-9_\-]+)+")
_ENDPOINT_RE = re.compile(r"endpoint(?:\[([A-Za-z_][A-Za-z0-9_]*)\])?\.([A-Za-z0-9_\-]+)")
_ROW_RE = re.compile(r"endpoint(?:\[([A-Za-z_][A-Za-z0-9_]*)\])?\.([A-Za-z0-9_\-]+)\.([A-Za-z0-9_\-]+)")
_SERVICE_RE = re.compile(r"service(?:\[([A-Za-z_][A-Za-z0-9_]*)\])?\.([A-Za-z0-9_\-]+)")


BlockVersions = Dict[int, Dict[str, Any]]


def parse_ref(node: Any) -> Optional[int]:
    """The block id when `node` is a block reference (an object whose only key is "$ref"), else None."""
    if isinstance(node, dict) and len(node) == 1 and isinstance(node.get(REF_KEY), str):
        if REF_RE.fullmatch(node[REF_KEY]):
            return int(node[REF_KEY])
    return None


def list_shape(node: Any) -> bool:
    """An object with "$list" (an array) and at most an optional "$label" (1-64 characters)."""
    if not isinstance(node, dict) or not isinstance(node.get(LIST_KEY), list) or set(node) - {LIST_KEY, LABEL_KEY}:
        return False
    label = node.get(LABEL_KEY, "")
    return LABEL_KEY not in node or (isinstance(label, str) and 1 <= len(label) <= 64)


def parse_list(node: Any) -> Optional[List[int]]:
    """The allowed block ids when `node` is a list (an object with "$list", a non-empty array of ids, and an
    optional "$label"), else None."""
    if list_shape(node) and node[LIST_KEY]:
        if all(isinstance(i, str) and REF_RE.fullmatch(i) for i in node[LIST_KEY]):
            return [int(i) for i in node[LIST_KEY]]
    return None


def list_label(node: Any) -> Optional[str]:
    """The item label of a list node, None if it has none."""
    return node.get(LABEL_KEY) if isinstance(node, dict) else None


class _Omit:
    def __repr__(self) -> str:
        return "OMIT"


OMIT = _Omit()


def finding(path: str, level: str, code: str, message: str) -> Dict[str, str]:
    return {"path": path, "level": level, "code": code, "message": message}


def has_errors(findings: Iterable[Dict[str, str]]) -> bool:
    return any(f["level"] == "error" for f in findings)


def pointer(parts: Iterable[Any]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _walk_strings(node: Any, path: Tuple[Any, ...] = ()):
    """Yields (path, string) for every string value and (path, key) markers for keys. Block references
    are yielded as ("ref", path, node) and not descended into; malformed ones as ("bad-ref", path, node).
    Lists likewise as ("list", path, node) and ("bad-list", path, node)."""
    if isinstance(node, dict):
        if parse_ref(node) is not None:
            yield ("ref", path, node)
            return
        if parse_list(node) is not None:
            yield ("list", path, node)
            return
        if REF_KEY in node:
            yield ("bad-ref", path, node)
        if LIST_KEY in node or LABEL_KEY in node:
            yield ("bad-list", path, node)
        for key, value in node.items():
            yield ("key", path + (key,), key)
            yield from _walk_strings(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_strings(value, path + (index,))
    elif isinstance(node, str):
        yield ("value", path, node)


def _placeholders(text: str) -> List[str]:
    return [m.group(1) for m in _TOKEN_RE.finditer(text) if m.group(0) != "$${"]


def _endpoint_ref(ref: str) -> Optional[Tuple[str, str]]:
    """(slot, field) for an endpoint placeholder, else None."""
    match = _ENDPOINT_RE.fullmatch(ref)
    return (match.group(1) or DEFAULT_SLOT, match.group(2)) if match else None


def _row_ref(ref: str) -> Optional[Tuple[str, str, str]]:
    """(slot, field, sub) for a list-row placeholder, else None."""
    match = _ROW_RE.fullmatch(ref)
    return (match.group(1) or DEFAULT_SLOT, match.group(2), match.group(3)) if match else None


def _service_ref(ref: str) -> Optional[Tuple[str, str]]:
    """(endpoint slot, field) for a service placeholder, else None."""
    match = _SERVICE_RE.fullmatch(ref)
    return (match.group(1) or DEFAULT_SLOT, match.group(2)) if match else None


def _valid_reference(ref: str) -> bool:
    return bool(_VARIABLE_NAME_RE.fullmatch(ref) or _DEVICE_PATH_RE.fullmatch(ref) or _ENDPOINT_RE.fullmatch(ref)
                or _ROW_RE.fullmatch(ref) or _SERVICE_RE.fullmatch(ref))


def _service_uses(body: Any) -> Dict[str, set]:
    """{endpoint slot: service field names} used directly in a body (not in its blocks)."""
    slots: Dict[str, set] = {}
    for kind, _, text in _walk_strings(body):
        if kind == "value":
            for ref in _placeholders(text):
                service = _service_ref(ref)
                if service:
                    slots.setdefault(service[0], set()).add(service[1])
    return slots


def _row_uses(body: Any) -> Dict[str, Dict[str, set]]:
    """{slot: {list field: sub names}} of the row placeholders used directly in a body (not in its blocks)."""
    slots: Dict[str, Dict[str, set]] = {}
    for kind, _, text in _walk_strings(body):
        if kind == "value":
            for ref in _placeholders(text):
                row = _row_ref(ref)
                if row:
                    slots.setdefault(row[0], {}).setdefault(row[1], set()).add(row[2])
    return slots


def _uses(body: Any) -> Tuple[set, set, Dict[str, set]]:
    """(variable names, device paths, {slot: field names}) used directly in a body (not in its blocks)."""
    variables, device, slots = set(), set(), {}
    for kind, _, text in _walk_strings(body):
        if kind != "value":
            continue
        for ref in _placeholders(text):
            endpoint = _endpoint_ref(ref)
            if endpoint:
                slots.setdefault(endpoint[0], set()).add(endpoint[1])
            elif _service_ref(ref) or _row_ref(ref):
                continue
            elif ref.startswith(DEVICE_PREFIX) and _DEVICE_PATH_RE.fullmatch(ref):
                device.add(ref[len(DEVICE_PREFIX):])
            elif _VARIABLE_NAME_RE.fullmatch(ref):
                variables.add(ref)
    return variables, device, slots


def _key(prefix: str, name: str) -> str:
    return f"{prefix}{COPY_SEP}{name}" if prefix else name


def _segment(part: Any) -> str:
    return f"[{part}]" if isinstance(part, int) else str(part)


def _copy_name(path: Tuple[Any, ...]) -> str:
    """Nearest object key; `<parent key>[<index>]` for a reference directly in an array."""
    if path and isinstance(path[-1], int):
        parent = next((p for p in reversed(path[:-1]) if isinstance(p, str)), "")
        return f"{parent}[{path[-1]}]"
    return str(path[-1]) if path else ""


def _relative_name(path: Tuple[Any, ...]) -> str:
    out = ""
    for part in path:
        out += _segment(part) if isinstance(part, int) or not out else "." + part
    return out


def _direct_names(body: Any) -> List[Tuple[str, Tuple[Any, ...], str, Any]]:
    """(name, path, "ref" | "list", block id | block ids) of the references and lists directly in a body.
    Copies and lists share one namespace, names made unique."""
    refs = [(path, (kind, parse_ref(node) if kind == "ref" else parse_list(node)))
            for kind, path, node in _walk_strings(body) if kind in ("ref", "list")]
    names = [_copy_name(path) for path, _ in refs]
    names = [_relative_name(path) if names.count(name) > 1 else name for name, (path, _) in zip(names, refs)]
    # relative names can still collide (a literal key "a.x" next to {"a": {"x": ...}}): number the later ones
    taken: set = set(names)
    unique: List[str] = []
    for name in names:
        candidate, number = name, 1
        while candidate in unique or (candidate != name and candidate in taken):
            number += 1
            candidate = f"{name}#{number}"
        unique.append(candidate)
    return [(name, path, kind, ref) for name, (path, (kind, ref)) in zip(unique, refs)]


def _direct_refs(body: Any) -> List[Tuple[str, Tuple[Any, ...], int]]:
    """(copy name, path, block id) of the references directly in a body."""
    return [(name, path, ref) for name, path, kind, ref in _direct_names(body) if kind == "ref"]


def _at(body: Any, path: Tuple[Any, ...]) -> Any:
    for part in path:
        body = body[part]
    return body


def lists(body: Any) -> List[Dict[str, Any]]:
    """The lists of a template body: name, path, allowed block ids and item label (None without "$label")."""
    return [{"key": name, "path": pointer(path), "block_ids": ids, "item_label": list_label(_at(body, path))}
            for name, path, kind, ids in _direct_names(body) if kind == "list"]


def check_entries(body: Any, lists_req: Dict[str, List[Dict[str, Any]]],
                  blocks: Optional[BlockVersions] = None) -> List[Dict[str, str]]:
    """Checks the entries chosen for the lists of a body (`{list key: [{"name", "block_id"}]}`)."""
    allowed = {item["key"]: set(item["block_ids"]) for item in lists(body)}
    findings: List[Dict[str, str]] = []
    for key, entries in (lists_req or {}).items():
        base = "/lists" + pointer([key])
        if key not in allowed:
            findings.append(finding(base, "error", "unknown-list", f"The template has no list '{key}'"))
            continue
        seen: set = set()
        for index, entry in enumerate(entries or []):
            name = entry.get("name")
            if not isinstance(name, str) or not ENTRY_NAME_RE.fullmatch(name):
                findings.append(finding(f"{base}/{index}/name", "error", "bad-entry-name",
                                        "An entry name must be 1-64 letters, digits, '_', '.' or '-'"))
            elif name in seen:
                findings.append(finding(f"{base}/{index}/name", "error", "duplicate-entry",
                                        f"'{name}' is in the list twice"))
            seen.add(name)
            if entry.get("block_id") not in allowed[key]:
                findings.append(finding(f"{base}/{index}/block_id", "error", "block-not-allowed",
                                        f"This block is not allowed in '{key}'"))
    return findings


def copies(body: Any, blocks: BlockVersions, prefix: str = "", _base: Tuple[Any, ...] = (),
           _stack: Tuple[int, ...] = ()) -> List[Dict[str, Any]]:
    """Every block copy in a body, depth first. `path` points into the rendered configuration."""
    out: List[Dict[str, Any]] = []
    for name, path, ref in _direct_refs(body):
        key = _key(prefix, name)
        block = (blocks or {}).get(ref)
        out.append({"key": key, "label": key.replace(COPY_SEP, " · "), "block_id": ref,
                    "name": block.get("name") if block else None,
                    "parent": prefix or None, "path": pointer(_base + path)})
        if block and ref not in _stack:
            out.extend(copies(block.get("body"), blocks, key, _base + path, _stack + (ref,)))
    return out


def inputs(body: Any, variables: List[Dict[str, Any]], blocks: Optional[BlockVersions]) -> Dict[str, Any]:
    """Everything a rendering needs: variables and endpoint slots of the body and of every block copy
    (keyed `<copy>/<name>`), the device fields used anywhere, and the copies."""
    all_copies = copies(body, blocks or {})
    bodies = [(None, body, variables)]
    for copy in all_copies:
        block = (blocks or {}).get(copy["block_id"])
        if block:
            bodies.append((copy["key"], block.get("body"), block.get("variables") or []))
    out_vars, out_endpoints, device = [], [], set()
    for copy_key, copy_body, copy_vars in bodies:
        _, used_device, slots = _uses(copy_body)
        services = _service_uses(copy_body)
        rows = _row_uses(copy_body)
        device |= used_device
        out_vars += [{**decl, "key": _key(copy_key or "", decl.get("name")), "copy": copy_key}
                     for decl in copy_vars if isinstance(decl.get("name"), str)]
        out_endpoints += [{"key": _key(copy_key or "", slot), "copy": copy_key, "slot": slot,
                           "fields": sorted(slots.get(slot, ())), "service_fields": sorted(services.get(slot, ())),
                           "row_fields": {f: sorted(subs) for f, subs in sorted(rows.get(slot, {}).items())}}
                          for slot in sorted(set(slots) | set(services) | set(rows))]
    out_lists = []
    for item in lists(body):
        entry_blocks = []
        for block_id in item["block_ids"]:
            block = (blocks or {}).get(block_id)
            entry_blocks.append({
                "block_id": block_id, "name": block.get("name") if block else None,
                "inputs": inputs(block.get("body"), block.get("variables") or [], blocks) if block
                else {"variables": [], "endpoints": [], "device": [], "copies": [], "lists": []}})
        out_lists.append({"key": item["key"], "label": item["key"].replace(COPY_SEP, " · "),
                          "item_label": item["item_label"], "path": item["path"], "blocks": entry_blocks})
    return {"variables": out_vars, "endpoints": out_endpoints, "device": sorted(device), "copies": all_copies,
            "lists": out_lists}


def references(body: Any, blocks: Optional[BlockVersions] = None) -> Dict[str, List[str]]:
    """Variable, device field and endpoint references used in a template body and its block copies.
    `endpoints` lists the endpoint slots (one endpoint is chosen per slot), `endpoint_fields` the
    `slot.field` pairs (and `slot.field.sub` for list rows); both keyed `<copy>/<slot>` inside copies."""
    found = inputs(body, [], blocks)
    variables = set(_uses(body)[0])
    for copy in found["copies"]:
        block = (blocks or {}).get(copy["block_id"])
        if block:
            variables |= {_key(copy["key"], name) for name in _uses(block.get("body"))[0]}
    return {"variables": sorted(variables), "device": found["device"],
            "endpoints": sorted(e["key"] for e in found["endpoints"]),
            "endpoint_fields": sorted([f"{e['key']}.{f}" for e in found["endpoints"] for f in e["fields"]]
                                      + [f"{e['key']}.{f}.{sub}" for e in found["endpoints"]
                                         for f, subs in e["row_fields"].items() for sub in subs])}


def coerce(value: Any, decl: Dict[str, Any]) -> Any:
    """Converts a supplied value to the declared type. Raises ValueError with a readable message."""
    var_type = decl.get("type", "string")
    if var_type in ("string", "text"):
        if isinstance(value, str):
            return value
        raise ValueError("expected a string")
    if var_type == "number":
        if isinstance(value, bool):
            raise ValueError("expected a number")
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                raise ValueError("expected a number") from None
            return int(number) if number.is_integer() and "." not in value and "e" not in value.lower() else number
        raise ValueError("expected a number")
    if var_type == "integer":
        if isinstance(value, bool):
            raise ValueError("expected an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                raise ValueError("expected an integer") from None
        raise ValueError("expected an integer")
    if var_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise ValueError("expected true or false")
    if var_type == "enum":
        options = decl.get("options") or []
        if value in options:
            return value
        for option in options:
            if isinstance(value, str) and str(option) == value:
                return option
        raise ValueError(f"must be one of {', '.join(map(str, options))}")
    raise ValueError(f"unknown type '{var_type}'")


def lint(body: Any, variables: List[Dict[str, Any]], kind: str = "template") -> List[Dict[str, str]]:
    """Static checks of a template or block (no values, no device). A template body must be an object,
    a block body an object or an array."""
    findings: List[Dict[str, str]] = []
    body_kind = kind
    if kind == "block":
        if not isinstance(body, (dict, list)):
            findings.append(finding("", "error", "body-not-object-or-array",
                                    "The block body must be a JSON object or array"))
    elif not isinstance(body, dict):
        findings.append(finding("", "error", "body-not-object", "The template body must be a JSON object"))

    declared: Dict[str, Dict[str, Any]] = {}
    for index, decl in enumerate(variables):
        path = f"/variables/{index}"
        name = decl.get("name")
        if not isinstance(name, str) or not _VARIABLE_NAME_RE.fullmatch(name):
            findings.append(finding(path, "error", "bad-variable-name",
                                    f"Variable name '{name}' must be a letter or _ followed by letters, digits or _"))
            continue
        if name == "device":
            findings.append(finding(path, "error", "reserved-variable-name", "'device' is reserved for device metadata"))
            continue
        if name in declared:
            findings.append(finding(path, "error", "duplicate-variable", f"Variable '{name}' is declared twice"))
            continue
        declared[name] = decl
        var_type = decl.get("type", "string")
        if var_type not in VARIABLE_TYPES:
            findings.append(finding(path, "error", "bad-variable-type", f"Variable '{name}' has unknown type '{var_type}'"))
            continue
        if var_type == "enum" and not decl.get("options"):
            findings.append(finding(path, "error", "enum-without-options", f"Enum variable '{name}' needs options"))
            continue

    used = set()
    endpoint_slots = set(_uses(body)[2]) | set(_row_uses(body))
    for kind, path, text in _walk_strings(body):
        if kind == "ref":
            continue
        if kind == "list":
            if body_kind == "block":
                findings.append(finding(pointer(path), "error", "list-in-block", "A block cannot contain a list"))
            elif any(isinstance(p, int) for p in path):
                findings.append(finding(pointer(path), "error", "list-in-array", "A list cannot be inside an array"))
            continue
        if kind == "bad-list":
            findings.append(finding(pointer(path), "error", "bad-list",
                                    "A list must be {\"$list\": [\"<block name>\", ...]} with at most a "
                                    "\"$label\" of 1-64 characters"))
            continue
        if kind == "bad-ref":
            findings.append(finding(pointer(path), "error", "bad-ref",
                                    "A block reference must be the only key, as {\"$ref\": \"<block name>\"}"))
            continue
        if kind == "key":
            if text in RESERVED_KEYS:
                findings.append(finding(pointer(path), "error", "reserved-key",
                                        f"'{text}' is reserved for a later version and not supported yet"))
            elif _placeholders(text):
                findings.append(finding(pointer(path), "warning", "placeholder-in-key",
                                        "Placeholders in keys are not substituted"))
            continue
        for ref in _placeholders(text):
            if not _valid_reference(ref):
                findings.append(finding(pointer(path), "error", "bad-placeholder", f"'${{{ref}}}' is not a valid placeholder"))
            elif ref.startswith(DEVICE_PREFIX) or _endpoint_ref(ref) or _row_ref(ref):
                continue
            elif _service_ref(ref):
                slot = _service_ref(ref)[0]
                if slot not in endpoint_slots:
                    findings.append(finding(pointer(path), "error", "service-without-endpoint",
                                            f"'${{{ref}}}' needs an endpoint placeholder of slot '{slot}' in the same body"))
            elif ref not in declared:
                findings.append(finding(pointer(path), "error", "unknown-variable", f"Variable '{ref}' is not declared"))
            else:
                used.add(ref)

    for index, decl in enumerate(variables):
        if decl.get("name") in declared and decl["name"] not in used:
            findings.append(finding(f"/variables/{index}", "warning", "unused-variable",
                                    f"Variable '{decl['name']}' is not used in the body"))
    return findings


def _text(value: Any) -> str:
    if value is OMIT or value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _scoped(entries: Optional[Dict[str, Any]], prefix: str) -> Optional[Dict[str, Any]]:
    """Entries keyed directly under a copy prefix (`<prefix>/<name>` -> `name`); unprefixed ones at top level."""
    if entries is None:
        return None
    start = prefix + COPY_SEP if prefix else ""
    return {k[len(start):]: v for k, v in entries.items()
            if k.startswith(start) and COPY_SEP not in k[len(start):]}


class _Renderer:
    def __init__(self, variables: List[Dict[str, Any]], values: Dict[str, Any], device: Optional[Dict[str, Any]],
                 endpoints: Optional[Dict[str, Optional[Dict[str, Any]]]], blocks: Optional[BlockVersions] = None,
                 prefix: str = "", stack: Tuple[int, ...] = (),
                 lists: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 services: Optional[Dict[str, Optional[Dict[str, Any]]]] = None):
        self.findings: List[Dict[str, str]] = []
        self.all_services = services
        self.services = _scoped(services, prefix)
        self.lists = lists or {}
        self.device = device
        self.all_values = values
        self.all_endpoints = endpoints
        self.blocks = blocks
        self.prefix = prefix
        self.stack = stack
        self.endpoints = _scoped(endpoints, prefix)
        self.scope: Dict[str, Any] = {}
        values = _scoped(values, prefix) or {}
        declared = {d["name"]: d for d in variables if isinstance(d.get("name"), str)}
        for name in values:
            if name not in declared:
                self.findings.append(finding(f"/values/{_key(prefix, name)}", "warning", "unknown-value",
                                             f"Value '{name}' is not a declared variable and is ignored"))
        for name, decl in declared.items():
            supplied = values.get(name)
            path = f"/values/{_key(prefix, name)}"
            if supplied is not None and supplied != "":
                try:
                    self.scope[name] = coerce(supplied, decl)
                except ValueError as exc:
                    self.findings.append(finding(path, "error", "bad-value", f"'{name}': {exc}"))
                    self.scope[name] = OMIT
            elif decl.get("required", True):
                self.findings.append(finding(path, "error", "missing-variable",
                                             f"'{decl.get('label') or name}' is required"))
                self.scope[name] = OMIT
            else:
                self.scope[name] = OMIT

    def _copy(self, name: str, ref: int, path: Tuple[Any, ...]) -> Any:
        if self.blocks is None:  # no block context: keep the reference as it is
            return {REF_KEY: str(ref)}
        block = self.blocks.get(ref)
        if block is None:
            self.findings.append(finding(pointer(path), "error", "missing-block",
                                         f"Block {ref} does not exist"))
            return OMIT
        if ref in self.stack:
            self.findings.append(finding(pointer(path), "error", "ref-cycle",
                                         f"Block {ref} includes itself"))
            return OMIT
        child = _Renderer(block.get("variables") or [], self.all_values, self.device, self.all_endpoints,
                          self.blocks, _key(self.prefix, name), self.stack + (ref,), services=self.all_services)
        rendered = child.node(block.get("body"), path)
        self.findings.extend(child.findings)
        return rendered

    def _resolve(self, ref: str, path: Tuple[Any, ...]) -> Any:
        service_ref = _service_ref(ref)
        if service_ref:
            slot, field = service_ref
            if self.services is None:  # no device context (template preview): keep the placeholder
                return "${" + ref + "}"
            service = self.services.get(slot)
            if service is None:
                self.findings.append(finding(pointer(path), "error", "missing-service",
                                             f"Choose a service for '{slot}'"))
                return OMIT
            if service.get(field) is None:
                self.findings.append(finding(pointer(path), "error", "missing-service-field",
                                             f"'{ref}': the chosen service has no '{field}'"))
                return OMIT
            return service[field]
        endpoint_ref = _endpoint_ref(ref)
        if endpoint_ref:
            slot, field = endpoint_ref
            if self.endpoints is None:  # no device context (template preview): keep the placeholder
                return "${" + ref + "}"
            endpoint = self.endpoints.get(slot)
            if endpoint is None:
                self.findings.append(finding(pointer(path), "error", "missing-endpoint",
                                             f"Choose an endpoint for '{slot}'"))
                return OMIT
            if endpoint.get(field) is None:
                self.findings.append(finding(pointer(path), "error", "missing-endpoint-field",
                                             f"'{ref}': the chosen endpoint has no '{field}'"))
                return OMIT
            return endpoint[field]
        if ref.startswith(DEVICE_PREFIX):
            node: Any = self.device
            if node is None:
                self.findings.append(finding(pointer(path), "error", "missing-device-field",
                                             f"'{ref}': the device has no metadata"))
                return OMIT
            for part in ref[len(DEVICE_PREFIX):].split("."):
                if not isinstance(node, dict) or part not in node:
                    self.findings.append(finding(pointer(path), "error", "missing-device-field",
                                                 f"'{ref}' is not set on this device"))
                    return OMIT
                node = node[part]
            return node
        return self.scope.get(ref, OMIT)

    def _string(self, text: str, path: Tuple[Any, ...]) -> Any:
        whole = _WHOLE_RE.fullmatch(text)
        if whole and _valid_reference(whole.group(1)):
            return self._resolve(whole.group(1), path)

        def replace(match: re.Match) -> str:
            if match.group(0) == "$${":
                return "${"
            ref = match.group(1)
            return _text(self._resolve(ref, path)) if _valid_reference(ref) else match.group(0)

        return _TOKEN_RE.sub(replace, text)

    def node(self, node: Any, path: Tuple[Any, ...] = (), names: Optional[Dict[Tuple[Any, ...], str]] = None) -> Any:
        if names is None:  # copy names of the references directly in this body
            names = {path + ref_path: name for name, ref_path, _, _ in _direct_names(node)}
        ref = parse_ref(node)
        if ref is not None:
            return self._copy(names[path], ref, path)
        if parse_list(node) is not None:
            out = {}
            for entry in self.lists.get(names[path]) or []:
                rendered = self._copy(_key(names[path], entry["name"]), entry["block_id"], path + (entry["name"],))
                if rendered is not OMIT:
                    out[entry["name"]] = rendered
            return out
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                rendered = self.node(value, path + (key,), names)
                if rendered is not OMIT:
                    out[key] = rendered
            return out
        if isinstance(node, list):
            rendered_items = (self.node(v, path + (i,), names) for i, v in enumerate(node))
            return [item for item in rendered_items if item is not OMIT]
        if isinstance(node, str):
            return self._string(node, path)
        return node


def render(
    body: Any,
    variables: List[Dict[str, Any]],
    values: Optional[Dict[str, Any]] = None,
    device: Optional[Dict[str, Any]] = None,
    endpoints: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
    blocks: Optional[BlockVersions] = None,
    kind: str = "template",
    lists: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    services: Optional[Dict[str, Optional[Dict[str, Any]]]] = None,
) -> Tuple[Optional[Any], List[Dict[str, str]]]:
    """Renders a template. Returns (config, findings); config is None if the template itself is invalid.
    `endpoints` maps each endpoint slot to the chosen endpoint's flat fields (see `endpoint_fields`);
    None leaves endpoint placeholders as they are (no device to choose from). `values` and `endpoints`
    of block copies are keyed `<copy>/<name>` (see `inputs`). `blocks` holds the referenced block
    versions; None leaves references as they are. `kind` "block" also renders an array body. `lists` maps
    each list to its entries `[{"name", "block_id"}]` (see `check_entries`); a list without entries renders `{}`.
    `services` maps endpoint slots (keyed like `endpoints`) to the chosen service's flat fields (see
    `service_fields`); None leaves service placeholders as they are."""
    findings = lint(body, variables, kind)
    if has_errors(findings):
        return None, findings
    values = values or {}
    renderer = _Renderer(variables, values, device, endpoints, blocks, lists=lists, services=services)
    config = renderer.node(body)
    copy_keys = {c["key"] for c in copies(body, blocks)} if blocks is not None else set()
    for list_key, _, node_kind, _ in _direct_names(body):  # list entries render like copies
        if node_kind != "list":
            continue
        for entry in (lists or {}).get(list_key) or []:
            entry_key = _key(list_key, str(entry.get("name")))
            copy_keys.add(entry_key)
            block = (blocks or {}).get(entry.get("block_id"))
            if block:
                copy_keys |= {c["key"] for c in copies(block.get("body"), blocks, entry_key)}
    unknown = [finding(f"/values/{k}", "warning", "unknown-value", f"Value '{k}' is not a declared variable and is ignored")
               for k in values if COPY_SEP in k and k.rsplit(COPY_SEP, 1)[0] not in copy_keys]
    return config, findings + renderer.findings + unknown


def _flat_fields(data: Dict[str, Any], type_mapping: Dict[str, str]) -> Dict[str, Any]:
    fields = {key: entry.get("value") for key, entry in (data or {}).items()}
    for field_key, role in type_mapping.items():
        if role not in fields and fields.get(field_key) is not None:
            fields[role] = fields[field_key]
    return fields


def endpoint_fields(endpoint: Dict[str, Any], type_mapping: Dict[str, str]) -> Dict[str, Any]:
    """Flat field values of a resolved endpoint, plus its role fields (`ip`, `name`) under the role name
    when the type maps them to a differently named field."""
    return _flat_fields(endpoint.get("endpoint_data"), type_mapping)


def service_fields(service: Dict[str, Any], type_mapping: Dict[str, str]) -> Dict[str, Any]:
    """Flat field values of a resolved service, plus its role fields (`port`) under the role name."""
    return _flat_fields(service.get("service_data"), type_mapping)
