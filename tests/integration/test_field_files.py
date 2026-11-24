"""File fields hold a reference object that only the upload/remove routes may create or change."""
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select, update

from db.models.device import Device
from db.models.device_type import DeviceType
from db.models.field_file import FieldFile
from db.sqlalchemy.endpoint import Endpoint
from db.sqlalchemy.service import Service
from tests.integration.endpoint_fixtures import (
    seed_endpoint, seed_endpoint_type, seed_service, seed_service_type,
)
from tests.integration.test_abac_authorization import AbacFixtures
from tests.pem_fixtures import make_cert, make_key

CERT = {"type": "file", "label": "Cert"}
REF = {"$file": "f-1", "name": "a.pem", "kind": "certificate", "not_after": None,
       "uploaded_at": "2026-01-01T00:00:00Z"}
CHANGE_MESSAGE = "'cert' can only be changed by uploading or removing a file"


def _message(response):
    # The device route wraps the repo's message in a string, so match by containment.
    return response.json()["message"]


async def _check_guard(patch, wrap, set_stored):
    """The same checks on every entity; `wrap` puts the data under the entity's request key."""
    rejected = await patch(wrap({"cert": "some text"}))
    assert rejected.status_code == 422 and "'cert' must be an uploaded file" in str(_message(rejected))

    await set_stored()
    echoed = await patch(wrap({"cert": REF}))
    assert echoed.status_code == 200, echoed.text

    other = await patch(wrap({"cert": {**REF, "$file": "f-2"}}))
    assert other.status_code == 422 and CHANGE_MESSAGE in str(_message(other))
    removed = await patch(wrap({"cert": None}))
    assert removed.status_code == 422 and CHANGE_MESSAGE in str(_message(removed))


async def test_save_cannot_set_or_change_a_file_reference(client, db_session):
    await AbacFixtures(db_session).setup(devices={"dev-1": {}})
    type_id = await seed_endpoint_type(db_session, {"cert": CERT})
    endpoint_id = await seed_endpoint(db_session, "dev-1", type_id)

    created = await client.post(
        "/endpoints", params={"device_id": "dev-1"},
        json={"type_id": type_id, "endpoint_data": {"cert": {"$file": "x", "name": "a"}}})
    assert created.status_code == 422 and CHANGE_MESSAGE in str(_message(created))

    async def set_stored():
        await db_session.execute(update(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
                                 .values(endpoint_data={"cert": REF}))
        await db_session.commit()

    await _check_guard(
        lambda body: client.patch(f"/endpoints/{endpoint_id}", json=body),
        lambda data: {"endpoint_data": data}, set_stored)

    # A reference under a key the type no longer defines is a custom value and can be cleared.
    await db_session.execute(update(Endpoint).where(Endpoint.endpoint_id == endpoint_id)
                             .values(endpoint_data={"old": REF}))
    await db_session.commit()
    cleared = await client.patch(f"/endpoints/{endpoint_id}", json={"endpoint_data": {"old": None}})
    assert cleared.status_code == 200, cleared.text
    assert "old" not in cleared.json()["endpoint_data"]


async def test_service_file_reference_is_protected(client, db_session):
    await AbacFixtures(db_session).setup(devices={"dev-1": {}})
    endpoint_id = await seed_endpoint(db_session, "dev-1", await seed_endpoint_type(db_session))
    service_id = await seed_service(db_session, endpoint_id, await seed_service_type(db_session, {"cert": CERT}))

    async def set_stored():
        await db_session.execute(update(Service).where(Service.service_id == service_id)
                                 .values(service_data={"cert": REF}))
        await db_session.commit()

    await _check_guard(
        lambda body: client.patch(f"/services/{service_id}", json=body),
        lambda data: {"service_data": data}, set_stored)


async def test_device_file_reference_is_protected(client, db_session):
    await AbacFixtures(db_session).setup(devices={"dev-1": {}})
    (await db_session.get(DeviceType, "default")).fields = {"cert": CERT}
    await db_session.commit()

    async def set_stored():
        await db_session.execute(update(Device).where(Device.device_id == "dev-1")
                                 .values(device_data={"cert": REF}))
        await db_session.commit()

    await _check_guard(lambda body: client.patch("/dev-1/metadata", json=body), lambda data: data, set_stored)


# --- upload, download, remove ---

SECRET_KEY = {"type": "file", "label": "Key", "secret": True}
STREAM = {"content-type": "application/octet-stream"}


@pytest.fixture(autouse=True)
def file_encryption_key(monkeypatch):
    monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _put(client, path, content, name="plc.crt"):
    return client.put(path, params={"name": name}, content=content, headers=STREAM)


async def _row_count(db_session):
    return (await db_session.execute(select(func.count()).select_from(FieldFile))).scalar_one()


async def _endpoint_with(db_session, fields):
    await AbacFixtures(db_session).setup(devices={"dev-1": {}})
    type_id = await seed_endpoint_type(db_session, fields)
    return type_id, await seed_endpoint(db_session, "dev-1", type_id)


async def test_upload_download_replace_remove(client, db_session):
    _, endpoint_id = await _endpoint_with(db_session, {"cert": CERT})
    path = f"/endpoints/{endpoint_id}/files/cert"
    pem = make_cert()

    uploaded = await _put(client, path, pem)
    assert uploaded.status_code == 200, uploaded.text
    ref = uploaded.json()
    assert set(ref) == {"$file", "name", "kind", "not_after", "uploaded_at"}
    assert ref["name"] == "plc.crt" and ref["kind"] == "certificate" and ref["not_after"].endswith("Z")

    shown = await client.get(f"/endpoints/{endpoint_id}")
    assert shown.json()["endpoint_data"]["cert"]["value"] == ref
    assert "BEGIN CERTIFICATE" not in shown.text
    stored = (await db_session.execute(select(FieldFile.content))).scalar_one()
    assert b"BEGIN CERTIFICATE" not in stored

    downloaded = await client.get(path)
    assert downloaded.status_code == 200
    assert downloaded.content == pem.replace(b"\r\n", b"\n")
    assert downloaded.headers["content-type"].startswith("application/x-pem-file")
    assert 'attachment; filename="plc.crt"' in downloaded.headers["content-disposition"]

    replaced = await _put(client, path, make_cert("other"), name="other.crt")
    assert replaced.json()["$file"] != ref["$file"]
    assert await _row_count(db_session) == 1

    assert (await client.delete(path)).status_code == 204
    assert (await client.get(f"/endpoints/{endpoint_id}")).json()["endpoint_data"]["cert"]["value"] is None
    missing = await client.get(path)
    assert missing.status_code == 404 and missing.json() == {"message": "No file"}
    assert (await client.delete(path)).status_code == 404


async def test_service_and_device_files(client, db_session):
    _, endpoint_id = await _endpoint_with(db_session, {})
    service_id = await seed_service(db_session, endpoint_id, await seed_service_type(db_session, {"cert": CERT}))
    (await db_session.get(DeviceType, "default")).fields = {"cert": CERT}
    await db_session.commit()

    for path in (f"/services/{service_id}/files/cert", "/devices/dev-1/files/cert"):
        pem = make_cert(path)
        ref = (await _put(client, path, pem)).json()
        assert (await client.get(path)).content == pem
        assert ref["name"] == "plc.crt"

    stored_service = (await db_session.execute(
        select(Service.service_data).where(Service.service_id == service_id))).scalar_one()
    stored_device = (await db_session.execute(
        select(Device.device_data).where(Device.device_id == "dev-1"))).scalar_one()
    assert stored_service["cert"]["$file"] and stored_device["cert"]["$file"]
    assert (await _put(client, "/devices/nope/files/cert", make_cert())).status_code == 404


async def test_secret_file_is_never_returned(client, db_session):
    type_id, endpoint_id = await _endpoint_with(db_session, {"key": SECRET_KEY})
    path = f"/endpoints/{endpoint_id}/files/key"
    uploaded = await _put(client, path, make_key(), name="plc.key")
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["kind"] == "private-key" and "PRIVATE KEY" not in uploaded.text

    denied = await client.get(path)
    assert denied.status_code == 403 and denied.json() == {"message": "This file is secret and cannot be downloaded"}

    patched = await client.patch(f"/endpoint-types/{type_id}", json={"fields": {"key": {"secret": False}}})
    assert patched.status_code == 200, patched.text
    assert (await client.get(path)).status_code == 403


async def test_upload_refusals(client, db_session, monkeypatch):
    _, endpoint_id = await _endpoint_with(db_session, {"cert": CERT, "x": {"type": "string", "label": "X"}})
    path = f"/endpoints/{endpoint_id}/files/cert"

    refused = await _put(client, path, make_cert(), name="plc\n.crt")
    assert refused.status_code == 422
    assert refused.json() == {"message": "The file name contains characters that are not allowed"}

    for body in (make_key(), make_cert() + make_key()):
        refused = await _put(client, path, body)
        assert refused.status_code == 422
        assert refused.json() == {"message": "'cert' holds a private key: mark the field as secret"}
    assert "PRIVATE KEY" not in refused.text

    for field in ("x", "unknown"):
        refused = await _put(client, f"/endpoints/{endpoint_id}/files/{field}", make_cert())
        assert refused.status_code == 422 and refused.json() == {"message": f"'{field}' is not a file field"}

    too_big = await _put(client, path, b"a" * 65537)
    assert too_big.status_code == 413 and too_big.json() == {"message": "file must not exceed 64 KB"}
    empty = await _put(client, path, b"")
    assert empty.status_code == 400 and empty.json() == {"message": "file is empty"}
    garbage = await _put(client, path, b"not a certificate")
    assert garbage.status_code == 422
    assert garbage.json() == {"message": "Expected a PEM certificate, private key or CRL, or a DER certificate"}

    monkeypatch.delenv("FILE_ENCRYPTION_KEY")
    unconfigured = await _put(client, path, make_cert())
    assert unconfigured.status_code == 503
    assert unconfigured.json() == {"message": "File storage is not configured (FILE_ENCRYPTION_KEY)"}
    assert await _row_count(db_session) == 0


class TestFilePermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {"oid": "reader-oid", "sub": "reader-oid", "preferred_username": "reader@test.com",
                "name": "Reader", "roles": []}

    async def test_reading_needs_read_and_writing_needs_write(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"reader": "reader-oid"},
            roles={"r": ["device.endpoint.read", "device.read"]},
            teams={"team": {"scope": None, "roles": ["r"], "users": ["reader"]}},
            devices={"dev-1": {}},
        )
        endpoint_id = await seed_endpoint(db_session, "dev-1", await seed_endpoint_type(db_session, {"cert": CERT}))
        path = f"/endpoints/{endpoint_id}/files/cert"

        assert (await _put(client, path, make_cert())).status_code == 403
        assert (await client.delete(path)).status_code == 403
        assert (await client.get(path)).status_code == 404  # allowed to look, nothing there
        assert (await _put(client, "/devices/dev-1/files/cert", make_cert())).status_code == 403
        assert await _row_count(db_session) == 0


async def test_files_follow_their_owner(client, db_session):
    type_id, endpoint_id = await _endpoint_with(db_session, {"cert": CERT})
    service_id = await seed_service(db_session, endpoint_id, await seed_service_type(db_session, {"cert": CERT}))
    await _put(client, f"/endpoints/{endpoint_id}/files/cert", make_cert())
    await _put(client, f"/services/{service_id}/files/cert", make_cert())
    assert await _row_count(db_session) == 2

    assert (await client.delete(f"/endpoints/{endpoint_id}")).status_code == 204
    assert await _row_count(db_session) == 0

    other = await seed_endpoint(db_session, "dev-1", type_id)
    await _put(client, f"/endpoints/{other}/files/cert", make_cert())
    assert (await client.patch(f"/endpoint-types/{type_id}", json={"fields": {"cert": None}})).status_code == 200
    cleared = await client.patch(f"/endpoints/{other}", json={"endpoint_data": {"cert": None}})
    assert cleared.status_code == 200, cleared.text
    assert await _row_count(db_session) == 0

    third = await seed_endpoint(db_session, "dev-1", type_id)
    await client.patch(f"/endpoint-types/{type_id}", json={"fields": {"cert": CERT}})
    await _put(client, f"/endpoints/{third}/files/cert", make_cert())
    assert (await client.delete(f"/endpoint-types/{type_id}", params={"cascade": True})).status_code == 204
    assert await _row_count(db_session) == 0


async def test_remove_leaves_a_plain_value_alone(client, db_session):
    type_id = await seed_endpoint_type(db_session, {"name": {"type": "string", "label": "Name"}})
    await AbacFixtures(db_session).setup(devices={"dev-1": {}})
    endpoint_id = await seed_endpoint(db_session, "dev-1", type_id, {"name": "plc"})

    response = await client.delete(f"/endpoints/{endpoint_id}/files/name")
    assert response.status_code == 404
    shown = await client.get(f"/endpoints/{endpoint_id}")
    assert shown.json()["endpoint_data"]["name"]["value"] == "plc"
