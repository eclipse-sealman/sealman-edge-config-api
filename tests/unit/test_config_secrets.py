import datetime

import pytest
from cryptography.fernet import Fernet

from db.repos.field_file import FieldFile
from file_crypto import encrypt
from routers.module_config.secrets import (
    SecretRestoreError, contains_mask, load_render_files, mask_config, mask_text, restore_secrets)


def test_mask_config():
    sha = "a1b2c3d4" + "0" * 56
    mask = mask_text("client.key", sha)
    assert mask == "<secret: client.key #a1b2c3d4>"
    config = {"a": {"key": "KEY"}, "list": ["x", {"k": "KEY2"}], "other": "keep"}
    paths = [{"path": "/a/key", "name": "client.key", "sha256": sha},
             {"path": "/list/1/k", "name": "client.key", "sha256": sha},
             {"path": "/gone/x", "name": "client.key", "sha256": sha},
             {"path": "/list/7", "name": "client.key", "sha256": sha}]
    masked = mask_config(config, paths)
    assert masked == {"a": {"key": mask}, "list": ["x", {"k": mask}], "other": "keep"}
    assert config["a"]["key"] == "KEY" and config["list"][1]["k"] == "KEY2"


OLD_SHA = "11111111" + "0" * 56
NEW_SHA = "22222222" + "0" * 56


class _Files:
    def __init__(self, rows):
        self.rows = rows

    async def get(self, owner_kind, owner_id, field):
        return self.rows.get((owner_kind, owner_id, field))


def _row(text, name="client.key", sha=NEW_SHA):
    return FieldFile(id="f-new", owner_kind="endpoint", owner_id="ep1", field="key", name=name,
                     content=encrypt(text), secret=True, sha256=sha, kind="private-key", subject=None,
                     not_after=None, uploaded_by="u", uploaded_at=datetime.datetime.now(datetime.timezone.utc))


def _entry(path, name="client.key", sha=OLD_SHA):
    return {"path": path, "owner_kind": "endpoint", "owner_id": "ep1", "field": "key", "name": name, "sha256": sha}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def test_restore_by_mask_follows_a_moved_value(key):
    previous = [_entry("/clients/0/clientPrivateKey")]
    mask = mask_text("client.key", OLD_SHA)
    content = {"clients": [{"name": "inserted"}, {"name": "plc", "clientPrivateKey": mask}]}
    files = _Files({("endpoint", "ep1", "key"): _row("NEW KEY PEM", name="renewed.key")})

    restored, paths = await restore_secrets(content, previous, files, by="mask")

    assert restored == {"clients": [{"name": "inserted"}, {"name": "plc", "clientPrivateKey": "NEW KEY PEM"}]}
    assert content["clients"][1]["clientPrivateKey"] == mask
    assert paths == [{**_entry("/clients/1/clientPrivateKey"), "name": "renewed.key", "sha256": NEW_SHA}]


async def test_restore_by_path_writes_the_current_file(key):
    previous = [_entry("/tls/key")]
    content = {"tls": {"key": "OLD KEY PEM"}, "other": 1}
    files = _Files({("endpoint", "ep1", "key"): _row("NEW KEY PEM")})

    restored, paths = await restore_secrets(content, previous, files, by="path")

    assert restored == {"tls": {"key": "NEW KEY PEM"}, "other": 1}
    assert paths == [{**_entry("/tls/key"), "sha256": NEW_SHA}]


async def test_restore_refuses_unknown_mask_and_missing_file(key, monkeypatch):
    previous = [_entry("/k")]
    with pytest.raises(SecretRestoreError) as exc:
        await restore_secrets({"x": "<secret: other.key #00000000>"}, previous, _Files({}), by="mask")
    assert str(exc.value) == "The value at /x is a secret placeholder that cannot be restored"

    removed = "The file 'client.key' (endpoint field 'key') has been removed"
    for by, content in (("mask", {"k": mask_text("client.key", OLD_SHA)}), ("path", {"k": "OLD KEY PEM"})):
        with pytest.raises(SecretRestoreError) as exc:
            await restore_secrets(content, previous, _Files({}), by=by)
        assert str(exc.value) == removed

    # a file that can no longer be decrypted has its own message, which never carries content
    files = _Files({("endpoint", "ep1", "key"): _row("NEW KEY PEM")})
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(SecretRestoreError) as exc:
        await restore_secrets({"k": "OLD KEY PEM"}, previous, files, by="path")
    assert str(exc.value) == "The file 'client.key' (endpoint field 'key') cannot be read: check FILE_ENCRYPTION_KEY"


async def test_a_file_that_cannot_be_decrypted_is_an_unreadable_file_finding(key, monkeypatch):
    from routers.module_config import templating

    row = _row("NEW KEY PEM")

    class _Many:
        async def get_many(self, ids):
            return {row.id: row}

    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    ref = {"$file": "f-new", "name": "client.key"}
    files = await load_render_files(_Many(), [("endpoint", "ep1", {"key": ref})])
    config, findings = templating.render({"k": "${endpoint.key}"}, [], {}, None, {"endpoint": {"key": ref}},
                                         files=files, secrets=[])
    assert [(f["path"], f["code"], f["message"]) for f in findings] == [
        ("/k", "unreadable-file", "'${endpoint.key}': the file cannot be read")]
    assert "NEW KEY" not in str(config)


async def test_restore_by_mask_drops_overwritten_secrets(key):
    previous = [_entry("/a"), _entry("/b")]
    content = {"a": "my own key text", "b": mask_text("client.key", OLD_SHA)}
    files = _Files({("endpoint", "ep1", "key"): _row("NEW KEY PEM")})

    restored, paths = await restore_secrets(content, previous, files, by="mask")

    assert restored == {"a": "my own key text", "b": "NEW KEY PEM"}
    assert [p["path"] for p in paths] == ["/b"]


async def test_mask_detection_is_exact(key):
    # a file name with a newline still gives a mask; a mask with a trailing newline is just a value
    mask = mask_text("a\nb.key", OLD_SHA)
    assert contains_mask({"k": mask})
    assert not contains_mask({"k": mask_text("client.key", OLD_SHA) + "\n"})
    with pytest.raises(SecretRestoreError):
        await restore_secrets({"x": mask}, [_entry("/k")], _Files({}), by="mask")

    previous = [_entry("/k", name="a\nb.key")]
    files = _Files({("endpoint", "ep1", "key"): _row("NEW KEY PEM")})
    restored, paths = await restore_secrets({"k": mask}, previous, files, by="mask")
    assert restored == {"k": "NEW KEY PEM"} and [p["path"] for p in paths] == ["/k"]
