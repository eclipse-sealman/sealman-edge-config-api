"""
Loading the block versions a template or block body references, transitively, with the checks that make a
reference valid. People write references by name (`{"$ref": "<block name>"}`); bodies are stored
with ids (`{"$ref": "<id>"}`) so renaming a block never changes a saved version. A reference always uses the
block's latest version.
"""
from typing import Any, Dict, Iterator, List, Optional, Tuple

from db.repos.module_config import ModuleConfigRepository
from routers.module_config import templating
from routers.module_config.templating import BlockVersions

Finding = Dict[str, str]
_INT4_MAX = 2147483647


def _refs(node: Any, path: Tuple[Any, ...] = ()) -> Iterator[Tuple[Tuple[Any, ...], int, bool]]:
    """(path, block id, in a list) of every reference and every block a `$list` allows; a list's blocks
    share the list's path."""
    ref = templating.parse_ref(node)
    allowed = templating.parse_list(node) if ref is None else None
    if ref is not None:
        yield path, ref, False
    elif allowed is not None:
        for block_id in allowed:
            yield path, block_id, True
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _refs(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _refs(value, path + (index,))


def refs_in(body: Any) -> List[int]:
    """Block ids of the references and lists directly in a body, in document order."""
    return [ref for _, ref, _ in _refs(body)]


async def load_blocks(
    repo: ModuleConfigRepository, body: Any, group_id: Optional[int], self_id: Optional[int] = None,
) -> Tuple[BlockVersions, List[Finding]]:
    """The latest version of every block referenced by `body` or allowed by one of its `$list`s, directly or
    through other blocks. A reference must name an existing block (not a template) of the group `group_id`, and
    never `self_id` (the block being saved) at any depth. `group_id=None` skips the group check (a saved version, checked when it was saved,
    keeps rendering after its row moved groups). Archived blocks load; with a group, a list allowing one gives an
    `archived-block` warning. A `$list` inside a block is an error (`list-in-block`). Findings point at the
    reference in `body` that leads to the problem."""
    blocks: BlockVersions = {}
    findings: List[Finding] = []
    templates: Dict[int, Optional[Dict[str, Any]]] = {}
    seen = set()
    reported: set = set()
    # (path of the top-level reference in `body`, body to scan)
    pending: List[Tuple[Optional[Tuple[Any, ...]], Any]] = [(None, body)]
    while pending:
        origin, node = pending.pop(0)
        for path, ref, in_list in _refs(node):
            at = templating.pointer(origin if origin is not None else path)
            if in_list and origin is not None:
                if (at, "list-in-block") not in reported:
                    reported.add((at, "list-in-block"))
                    findings.append(templating.finding(at, "error", "list-in-block",
                                                       "A block used here contains a list, which blocks cannot"))
                continue
            if ref in seen:
                continue
            seen.add(ref)
            if self_id is not None and ref == self_id:
                findings.append(templating.finding(at, "error", "self-reference", "The block includes itself"))
                continue
            template = None
            version = None
            if ref <= _INT4_MAX:  # larger ids cannot exist (int4 column)
                if ref not in templates:
                    templates[ref] = await repo.get_template(ref)
                template = templates[ref]
                version = await repo.get_version(ref) if template else None
            if version is None:
                findings.append(templating.finding(at, "error", "missing-block", "The referenced block does not exist"))
                continue
            if template["kind"] != "block":
                findings.append(templating.finding(at, "error", "not-a-block",
                                                   f"'{template['name']}' is a template, not a block"))
                continue
            if group_id is not None and template["group_id"] != group_id:
                findings.append(templating.finding(at, "error", "other-group",
                                                   f"Block '{template['name']}' belongs to another template group"))
                continue
            blocks[ref] = {"body": version["body"], "variables": version["variables"],
                           "slots": version.get("slots") or [], "name": template["name"]}
            pending.append((origin if origin is not None else path, version["body"]))
    if group_id is not None:
        for path, ref, in_list in _refs(body):
            if in_list and ref in blocks and templates[ref]["archived"]:
                findings.append(templating.finding(templating.pointer(path), "warning", "archived-block",
                                                   f"Block '{templates[ref]['name']}' is archived"))
    return blocks, findings


def _is_ref(node: Any) -> bool:
    return isinstance(node, dict) and len(node) == 1 and isinstance(node.get(templating.REF_KEY), str)


def _is_list(node: Any) -> bool:
    return (templating.list_shape(node) and all(isinstance(name, str) for name in node[templating.LIST_KEY]))


def _with_label(node: Dict[str, Any], stored: Dict[str, Any]) -> Dict[str, Any]:
    """`stored` plus the item label of list `node`, if it has one."""
    return {**stored, templating.LABEL_KEY: node[templating.LABEL_KEY]} if templating.LABEL_KEY in node else stored


async def resolve_names(repo: ModuleConfigRepository, body: Any) -> Tuple[Any, List[Finding]]:
    """`body` with every `{"$ref": "<block name>"}` turned into the stored `{"$ref": "<id>"}`, and every
    `{"$list": [<block names>]}` into `{"$list": [<ids>]}` (a `"$label"` is kept). An unknown name gives a `missing-block` finding
    and the reference or list stays as written."""
    rows: Optional[Dict[str, Dict]] = None
    findings: List[Finding] = []

    async def resolve(name: str, path: Tuple[Any, ...]) -> Optional[str]:
        nonlocal rows
        if not name:
            return None  # lint reports the malformed reference
        if rows is None:
            rows = {row["name"]: row for row in await repo.list_templates(include_archived=True)}
        row = rows.get(name)
        if row is None:
            findings.append(templating.finding(templating.pointer(path), "error", "missing-block",
                                               f"There is no block named '{name}'"))
            return None
        return str(row["id"])

    async def walk(node: Any, path: Tuple[Any, ...]) -> Any:
        if _is_ref(node):
            stored = await resolve(node[templating.REF_KEY], path)
            return {templating.REF_KEY: stored} if stored else node
        if _is_list(node):
            stored_ids = [await resolve(name, path) for name in node[templating.LIST_KEY]]
            return _with_label(node, {templating.LIST_KEY: stored_ids}) if all(stored_ids) else node
        if isinstance(node, dict):
            return {key: await walk(value, path + (key,)) for key, value in node.items()}
        if isinstance(node, list):
            return [await walk(value, path + (index,)) for index, value in enumerate(node)]
        return node

    return await walk(body, ()), findings


def show_names(body: Any, blocks: Optional[BlockVersions]) -> Any:
    """`body` with every stored reference and list written with block names (the inverse of `resolve_names`);
    blocks not in `blocks` stay as stored."""
    ref = templating.parse_ref(body)
    if ref is not None:
        block = (blocks or {}).get(ref)
        return {templating.REF_KEY: block["name"]} if block else body
    allowed = templating.parse_list(body)
    if allowed is not None:
        return _with_label(body, {templating.LIST_KEY: [blocks[i]["name"] if i in (blocks or {}) else str(i)
                                                        for i in allowed]})
    if isinstance(body, dict):
        return {key: show_names(value, blocks) for key, value in body.items()}
    if isinstance(body, list):
        return [show_names(value, blocks) for value in body]
    return body


async def referenced_by_others(repo: ModuleConfigRepository, template_id: int, group_id: int) -> bool:
    """Whether another non-archived row of the group references `template_id` in its latest version."""
    for row in await repo.list_templates(group_id=group_id):
        version = await repo.get_version(row["id"]) if row["id"] != template_id else None
        if version and any(ref == template_id for ref in refs_in(version["body"])):
            return True
    return False
