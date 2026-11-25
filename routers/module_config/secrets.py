"""Secret files in rendered module configs: masking, loading the files a render needs and restoring the
current file text where a stored or edited configuration carries a secret."""
import copy
import re
from typing import Any, Collection, Dict, Iterable, List, Literal, Optional, Set, Tuple

from db.repos.field_file import FieldFile, FieldFileRepository
from field_validation import is_file_ref
from file_crypto import FileEncryptionError, decrypt
from routers.module_config.templating import AppliedFiles, DefaultFiles, RenderFile


# Used with fullmatch only; DOTALL so a file name with a line break still makes a mask.
MASK_RE = re.compile(r"<secret: .+ #[0-9a-f]{8}>", re.DOTALL)


class SecretRestoreError(Exception):
    """A secret cannot be restored. The message names the path or file, never its content."""


def mask_text(name: str, sha256: str) -> str:
    return f"<secret: {name} #{sha256[:8]}>"


def mask_config(config: Any, secret_paths: List[Dict[str, str]]) -> Any:
    """A deep copy of `config` with the mask at each secret path; a path that no longer exists is skipped."""
    masked = copy.deepcopy(config)
    for entry in secret_paths:
        _set_at(masked, entry["path"], mask_text(entry["name"], entry["sha256"]))
    return masked


def _set_at(root: Any, pointer: str, value: str) -> bool:
    """Sets the value at the JSON pointer if it exists; False if it does not."""
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]
    if not parts:
        return False
    node = root
    for part in parts[:-1]:
        node = _child(node, part)
        if node is None:
            return False
    return _set(node, parts[-1], value)


def _set(node: Any, part: str, value: str) -> bool:
    if isinstance(node, dict) and part in node:
        node[part] = value
        return True
    if isinstance(node, list) and part.isdigit() and int(part) < len(node):
        node[int(part)] = value
        return True
    return False


def _child(node: Any, part: str) -> Any:
    if isinstance(node, dict):
        return node.get(part)
    if isinstance(node, list) and part.isdigit() and int(part) < len(node):
        return node[int(part)]
    return None


# (owner kind, owner id, flat field values) of an entity being rendered
RenderSource = Tuple[str, str, Optional[Dict[str, Any]]]


async def load_render_files(files_repo: FieldFileRepository, sources: Iterable[RenderSource]) -> Dict[str, RenderFile]:
    """The decrypted files of the entities being rendered. Only a reference that is the whole value of a
    top-level field is loaded, and only if the stored file belongs to that entity and field: a reference
    nested in another value or pointing at a file of another owner is left out, so the renderer reports it
    as missing, as it does for a file that is gone. A file that cannot be decrypted comes without text."""
    wanted: Dict[str, Set[Tuple[str, str, str]]] = {}
    for owner_kind, owner_id, fields in sources:
        for key, value in (fields or {}).items():
            if is_file_ref(value):
                wanted.setdefault(value["$file"], set()).add((owner_kind, owner_id, key))
    if not wanted:
        return {}
    out: Dict[str, RenderFile] = {}
    for file_id, row in (await files_repo.get_many(wanted)).items():
        if (row.owner_kind, row.owner_id, row.field) not in wanted.get(file_id, ()):
            continue
        out[file_id] = _render_file(row)
    return out


def _render_file(row: FieldFile) -> RenderFile:
    """A stored file for the renderer; one that cannot be decrypted comes without text."""
    try:
        text: Optional[str] = decrypt(row.content)
    except FileEncryptionError:
        text = None
    return RenderFile(text=text, secret=row.secret, name=row.name, sha256=row.sha256,
                      owner_kind=row.owner_kind, owner_id=row.owner_id, field=row.field)


# (id of the template or block that declares it, variable declaration)
DefaultSource = Tuple[str, Dict[str, Any]]


async def load_variable_files(files_repo: FieldFileRepository, defaults: Iterable[DefaultSource]) -> DefaultFiles:
    """The decrypted default files of the file variables declared with a default, by (owner id, variable name).
    Each is the file stored for that template and variable, whatever `$file` id the declaration names: the file
    of a variable is always the one uploaded last, and an id never pulls in another owner's file. A variable
    without a stored file is left out (the renderer reports it as missing)."""
    out: DefaultFiles = {}
    for owner_id, decl in defaults:
        name = decl.get("name")
        if decl.get("type") != "file" or decl.get("default") is None or not isinstance(name, str):
            continue
        if (owner_id, name) not in out:
            row = await files_repo.get("template", owner_id, name)
            if row is not None:
                out[(owner_id, name)] = _render_file(row)
    return out


def module_owner(device: str, module: str) -> str:
    """Owner id of the files uploaded when applying a template to a device's module (owner kind `module`)."""
    return f"{device}/{module}"


async def load_applied_files(files_repo: FieldFileRepository, device: str, module: str, values: Dict[str, Any],
                             file_keys: Collection[str]) -> AppliedFiles:
    """The decrypted files uploaded when applying, by variable key: for each file variable key whose value is a
    file reference, the file stored for this device's module and that key, whatever `$file` id the value names
    (an id never pulls in another owner's file). A value of a key that is not a file variable loads nothing;
    a key without a stored file is left out (the renderer reports it as missing)."""
    out: AppliedFiles = {}
    for key in file_keys:
        if is_file_ref(values.get(key)):
            row = await files_repo.get("module", module_owner(device, module), key)
            if row is not None:
                out[key] = _render_file(row)
    return out


def _strings(node: Any, pointer: str = ""):
    """Every string value in `node` with its JSON pointer."""
    if isinstance(node, str):
        yield pointer, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _strings(value, f"{pointer}/{index}")


def contains_mask(content: Any) -> bool:
    return any(MASK_RE.fullmatch(value) for _, value in _strings(content))


def mask_pointers(content: Any) -> List[str]:
    """The JSON pointers of the strings in `content` that look like a mask."""
    return [pointer for pointer, value in _strings(content) if MASK_RE.fullmatch(value)]


async def restore_secrets(
    content: Any, previous: List[Dict[str, str]], files_repo: FieldFileRepository, *, by: Literal["path", "mask"],
) -> Tuple[Any, List[Dict[str, str]]]:
    """A copy of `content` with the current text of the files of `previous` (the secret paths of a revision)
    and the new secret paths (current file name and sha256).

    `by="path"` (rollback): every path of `previous` gets the current file of its owner and field.
    `by="mask"` (write-back of an edited configuration): every string equal to the mask of an entry of
    `previous`, wherever it stands, gets that entry's current file; any other mask-like string is refused.
    Raises SecretRestoreError for such a string, for a file that is gone and for one that cannot be decrypted."""
    restored = copy.deepcopy(content)
    current: Dict[Tuple[str, str, str], Tuple[Any, str]] = {}

    async def file_of(entry: Dict[str, str]) -> Tuple[Any, str]:
        owner = (entry["owner_kind"], entry["owner_id"], entry["field"])
        if owner not in current:
            row = await files_repo.get(*owner)
            where = f"({entry['owner_kind']} field '{entry['field']}')"
            if row is None:
                raise SecretRestoreError(f"The file '{entry['name']}' {where} has been removed")
            try:
                text = decrypt(row.content)
            except FileEncryptionError:
                raise SecretRestoreError(
                    f"The file '{row.name}' {where} cannot be read: check FILE_ENCRYPTION_KEY") from None
            current[owner] = (row, text)
        return current[owner]

    paths: List[Dict[str, str]] = []

    async def put(entry: Dict[str, str], pointer: str) -> None:
        row, text = await file_of(entry)
        if _set_at(restored, pointer, text):
            paths.append({**entry, "path": pointer, "name": row.name, "sha256": row.sha256})

    if by == "path":
        for entry in previous:
            await put(entry, entry["path"])
        return restored, paths

    by_mask: Dict[str, List[Dict[str, str]]] = {}
    for entry in previous:
        by_mask.setdefault(mask_text(entry["name"], entry["sha256"]), []).append(entry)
    for pointer, value in list(_strings(content)):
        if not MASK_RE.fullmatch(value):
            continue
        entries = by_mask.get(value)
        if not entries:
            raise SecretRestoreError(f"The value at {pointer} is a secret placeholder that cannot be restored")
        # the same file text at several paths has one mask: prefer the entry recorded at this path
        await put(next((e for e in entries if e["path"] == pointer), entries[0]), pointer)
    return restored, paths
