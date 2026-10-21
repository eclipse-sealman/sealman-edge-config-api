import pytest
from fastapi import HTTPException
from starlette.requests import Request

from extensions import core_api, runtime
from extensions.security import hash_key

_ROWS = {
    "widgets": {"name": "widgets", "enabled": True, "endpoint_fields": {}, "internal_key_hash": hash_key("widgets-key")},
    "gadgets": {"name": "gadgets", "enabled": True, "endpoint_fields": {}, "internal_key_hash": hash_key("gadgets-key")},
    "sleepy": {"name": "sleepy", "enabled": False, "endpoint_fields": {}, "internal_key_hash": hash_key("sleepy-key")},
}


class _FakeExtensionRepo:
    async def get_extension_row(self, name):
        return _ROWS.get(name)


@pytest.fixture
def logged(monkeypatch):
    entries = []

    async def fake_log(user, access, method=None):
        entries.append((user, access))

    monkeypatch.setattr(runtime.AuditTrail, "log", fake_log)
    monkeypatch.setattr(core_api.AuditTrail, "log", fake_log)
    return entries


def _request(name: str) -> Request:
    path = f"/core/extensions/{name}/endpoints"
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""})


async def _call(name: str, key):
    return await core_api.core_extension_caller(
        name, _request(name), x_internal_key=key, extension_repo=_FakeExtensionRepo()
    )


async def _expect_status(name: str, key, status: int) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        await _call(name, key)
    assert exc_info.value.status_code == status
    return exc_info.value


@pytest.mark.asyncio
async def test_missing_key_is_401(logged):
    exc = await _expect_status("widgets", None, 401)
    assert exc.detail == "Missing X-Internal-Key"


@pytest.mark.asyncio
async def test_wrong_key_is_401(logged):
    exc = await _expect_status("widgets", "wrong-key", 401)
    assert exc.detail == "Invalid X-Internal-Key"


@pytest.mark.asyncio
async def test_key_of_another_extension_is_401(logged):
    exc = await _expect_status("widgets", "gadgets-key", 401)
    assert exc.detail == "Invalid X-Internal-Key"


@pytest.mark.asyncio
async def test_unknown_extension_is_401(logged):
    exc = await _expect_status("nobody", "widgets-key", 401)
    assert exc.detail == "Invalid X-Internal-Key"


@pytest.mark.asyncio
async def test_disabled_extension_is_403(logged):
    exc = await _expect_status("sleepy", "sleepy-key", 403)
    assert exc.detail == "Extension 'sleepy' is disabled"
    assert any(user == "extension:sleepy" and "rejected: extension disabled" in access for user, access in logged)


@pytest.mark.asyncio
async def test_valid_key_returns_row_and_audits_extension(logged):
    row = await _call("widgets", "widgets-key")
    assert row is _ROWS["widgets"]
    assert logged[0][0] == "extension:widgets"
    assert "internal GET /core/extensions/widgets/endpoints" in logged[0][1]


def test_managed_keys_by_type_keeps_only_own_keys_sorted():
    types = [
        {"type_id": "a", "managed_by": {"z_key": "widgets", "a_key": "widgets", "o": "gadgets"}},
        {"type_id": "b", "managed_by": {}},
        {"type_id": "c", "managed_by": {"o": "gadgets"}},
        {"type_id": "d"},
    ]
    assert core_api.managed_keys_by_type(types, "widgets") == {"a": ["a_key", "z_key"]}


def test_project_endpoint_keeps_only_stored_listed_keys():
    row = {
        "endpoint_id": "e1",
        "device_id": "d1",
        "type_id": "t1",
        "created_at": "c",
        "updated_at": "u",
        "endpoint_data": {"serial_number": "SN", "note": "n"},
    }
    assert core_api.project_endpoint(row, ["asset_tag", "serial_number"]) == {
        "endpoint_id": "e1",
        "device_id": "d1",
        "type_id": "t1",
        "created_at": "c",
        "updated_at": "u",
        "data": {"serial_number": "SN"},
    }
