"""File fields hold a reference object that only the upload/remove routes may create or change."""
from sqlalchemy import update

from db.models.device import Device
from db.models.device_type import DeviceType
from db.sqlalchemy.endpoint import Endpoint
from db.sqlalchemy.service import Service
from tests.integration.endpoint_fixtures import (
    seed_endpoint, seed_endpoint_type, seed_service, seed_service_type,
)
from tests.integration.test_abac_authorization import AbacFixtures

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
