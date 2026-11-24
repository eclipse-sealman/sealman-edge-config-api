"""Secret files in rendered module configs: masking and loading the files a render needs."""
import copy
from typing import Any, Dict, List

from db.repos.field_file import FieldFileRepository
from field_validation import is_file_ref
from file_crypto import FileEncryptionError, decrypt
from routers.module_config.templating import RenderFile


def mask_text(name: str, sha256: str) -> str:
    return f"<secret: {name} #{sha256[:8]}>"


def mask_config(config: Any, secret_paths: List[Dict[str, str]]) -> Any:
    """A deep copy of `config` with the mask at each secret path; a path that no longer exists is skipped."""
    masked = copy.deepcopy(config)
    for entry in secret_paths:
        parts = [p.replace("~1", "/").replace("~0", "~") for p in entry["path"].split("/")[1:]]
        if not parts:
            continue
        node = masked
        for part in parts[:-1]:
            node = _child(node, part)
            if node is None:
                break
        else:
            last = parts[-1]
            if isinstance(node, dict) and last in node:
                node[last] = mask_text(entry["name"], entry["sha256"])
            elif isinstance(node, list) and last.isdigit() and int(last) < len(node):
                node[int(last)] = mask_text(entry["name"], entry["sha256"])
    return masked


def _child(node: Any, part: str) -> Any:
    if isinstance(node, dict):
        return node.get(part)
    if isinstance(node, list) and part.isdigit() and int(part) < len(node):
        return node[int(part)]
    return None


def _file_ids(node: Any, found: set) -> None:
    if is_file_ref(node):
        found.add(node["$file"])
    elif isinstance(node, dict):
        for value in node.values():
            _file_ids(value, found)
    elif isinstance(node, list):
        for value in node:
            _file_ids(value, found)


async def load_render_files(files_repo: FieldFileRepository, *sources: Any) -> Dict[str, RenderFile]:
    """The decrypted files referenced anywhere in `sources` (dicts and lists). A file that is gone or cannot
    be decrypted is left out, so the renderer reports it as missing."""
    ids: set = set()
    for source in sources:
        _file_ids(source, ids)
    if not ids:
        return {}
    out: Dict[str, RenderFile] = {}
    for file_id, row in (await files_repo.get_many(ids)).items():
        try:
            text = decrypt(row.content)
        except FileEncryptionError:
            continue
        out[file_id] = RenderFile(text=text, secret=row.secret, name=row.name, sha256=row.sha256,
                                  owner_kind=row.owner_kind, owner_id=row.owner_id, field=row.field)
    return out
