import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.models.device import Device
from db.models.extension import Extension, ExtensionDeviceKey
from db.sqlalchemy.extension import SqlAlchemyExtensionRepository
from extensions.security import hash_key
from tests.integration.test_abac_authorization import AbacFixtures
from tests.integration.test_extensions_management_routes import _sample_registration, _unique_name


async def _setup(client, db_session):
    await AbacFixtures(db_session).setup(devices={"key-device": {"region": "EU"}})
    name = _unique_name("key_lifecycle")
    response = await client.post("/extensions", json=_sample_registration(name))
    assert response.status_code == 201
    return name, response.json()


async def test_internal_key_timestamps_and_authentication(client, db_session):
    name, created = await _setup(client, db_session)
    created_at = created["internal_key_created_at"]
    assert datetime.fromisoformat(created_at).tzinfo is not None
    assert created.get("internal_key_rotated_at") is None

    repo = SqlAlchemyExtensionRepository(db_session)
    previous_key = created["internal_key"]
    previous_rotated_at = None
    for _ in range(2):
        response = await client.post(f"/extensions/{name}/internal-key/rotate")
        assert response.status_code == 200
        key = response.json()["internal_key"]
        detail = (await client.get(f"/extensions/{name}")).json()
        assert detail["internal_key_created_at"] == created_at
        rotated_at = datetime.fromisoformat(detail["internal_key_rotated_at"])
        assert rotated_at >= datetime.fromisoformat(created_at)
        if previous_rotated_at is not None:
            assert rotated_at > previous_rotated_at
        row = await repo.get_extension_row(name)
        assert row["internal_key_hash"] == hash_key(key)
        assert row["internal_key_hash"] != hash_key(previous_key)
        assert "internal_key" not in detail and "internal_key_hash" not in detail
        previous_key, previous_rotated_at = key, rotated_at

    replaced = await client.put(f"/extensions/{name}", json=_sample_registration(name))
    assert replaced.status_code == 200
    assert replaced.json()["internal_key_created_at"] == created_at
    assert datetime.fromisoformat(replaced.json()["internal_key_rotated_at"]) == previous_rotated_at
    assert (await client.delete(f"/extensions/{name}")).status_code == 204
    recreated = (await client.post("/extensions", json=_sample_registration(name))).json()
    assert datetime.fromisoformat(recreated["internal_key_created_at"]) > datetime.fromisoformat(created_at)
    assert recreated.get("internal_key_rotated_at") is None


@pytest.mark.parametrize("timestamp", ["internal_key_created_at", "internal_key_rotated_at"])
async def test_key_timestamps_are_not_manifest_inputs(client, timestamp):
    name = _unique_name("readonly_key_dates")
    registration = _sample_registration(name)
    registration[timestamp] = "2000-01-01T00:00:00Z"
    assert (await client.post("/extensions", json=registration)).status_code == 400


async def test_legacy_internal_key_creation_time_stays_unknown(client, db_session):
    name = _unique_name("legacy_key")
    repo = SqlAlchemyExtensionRepository(db_session)
    await repo.create_extension(name, "", 1)
    row = (await db_session.execute(select(Extension).where(Extension.name == name))).scalar_one()
    row.internal_key_hash = hash_key("legacy-test-key")
    await db_session.commit()
    await repo.set_internal_key_hash(name, hash_key("replacement-test-key"))
    detail = await repo.get_extension_row(name)
    assert detail["internal_key_created_at"] is None
    assert detail["internal_key_rotated_at"] is not None


async def test_device_key_metadata_history_and_uuid_revocation(client, db_session):
    name, _ = await _setup(client, db_session)
    payload = {
        "device_id": "key-device", "module_id": "module-1",
        "label": "Uploader", "description": "Device upload credential",
    }
    first = (await client.post(f"/extensions/{name}/device-keys", json=payload)).json()
    UUID(first["id"])
    listed = (await client.get(f"/extensions/{name}/device-keys")).json()
    assert len(listed) == 1
    original = listed[0]
    assert original["label"] == payload["label"]
    assert original["description"] == payload["description"]
    assert original["module_id"] == "module-1"
    assert original.get("revoked_at") is None
    assert "key_hash" not in original and "device_key" not in original

    second = (await client.post(
        f"/extensions/{name}/device-keys", json={"device_id": "key-device", "label": "Replacement"},
    )).json()
    history = (await client.get(f"/extensions/{name}/device-keys")).json()
    assert len(history) == 2
    old = next(k for k in history if k["id"] == first["id"])
    assert old["created_at"] == original["created_at"]
    assert old["label"] == "Uploader"
    assert old["revoked_at"]
    assert (await client.delete(f"/extensions/{name}/device-keys/by-id/{first['id']}")).status_code == 404
    assert (await client.delete(f"/extensions/{name}/device-keys/by-id/{second['id']}")).status_code == 204
    assert (await client.delete(f"/extensions/{name}/device-keys/by-id/{second['id']}")).status_code == 404
    assert (await client.delete(f"/extensions/{name}/device-keys/by-id/{uuid4()}")).status_code == 404
    assert (await client.delete(f"/extensions/{name}/device-keys/by-id/not-a-uuid")).status_code == 400
    assert all(k["revoked_at"] for k in (await client.get(f"/extensions/{name}/device-keys")).json())
    repo = SqlAlchemyExtensionRepository(db_session)
    assert await repo.resolve_device_key(name, hash_key(first["device_key"])) is None
    assert await repo.resolve_device_key(name, hash_key(second["device_key"])) is None


async def test_uuid_revocation_is_extension_bound(client, db_session):
    name, _ = await _setup(client, db_session)
    other = _unique_name("other_key_owner")
    await client.post("/extensions", json=_sample_registration(other))
    issued = (await client.post(
        f"/extensions/{name}/device-keys", json={"device_id": "key-device"},
    )).json()
    assert (await client.delete(f"/extensions/{other}/device-keys/by-id/{issued['id']}")).status_code == 404
    repo = SqlAlchemyExtensionRepository(db_session)
    assert await repo.resolve_device_key(name, hash_key(issued["device_key"])) == "key-device"
    assert await repo.resolve_device_key(other, hash_key(issued["device_key"])) is None


async def test_database_enforces_one_active_key_but_retains_history(client, db_session):
    name, _ = await _setup(client, db_session)
    await client.post(f"/extensions/{name}/device-keys", json={"device_id": "key-device"})
    repo = SqlAlchemyExtensionRepository(db_session)
    await repo.create_device_key(name, "key-device", None, hash_key("replacement-test-key"))
    extension_id = (await db_session.execute(select(Extension.id).where(Extension.name == name))).scalar_one()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(ExtensionDeviceKey(
                extension_id=extension_id, device_id="key-device", key_hash=hash_key("duplicate-test-key"),
            ))
            await db_session.flush()
    history = await repo.list_device_keys(name)
    assert len(history) == 2
    assert sum(k["revoked_at"] is None for k in history) == 1


async def test_failed_replacement_preserves_active_key(client, db_session):
    name, _ = await _setup(client, db_session)
    issued = (await client.post(
        f"/extensions/{name}/device-keys", json={"device_id": "key-device"},
    )).json()
    repo = SqlAlchemyExtensionRepository(db_session)

    def reject_insert(mapper, connection, target):
        raise RuntimeError("Simulated key insertion failure")

    event.listen(ExtensionDeviceKey, "before_insert", reject_insert)
    try:
        with pytest.raises(RuntimeError, match="Simulated key insertion failure"):
            await repo.create_device_key(name, "key-device", None, hash_key("failed-replacement"))
    finally:
        event.remove(ExtensionDeviceKey, "before_insert", reject_insert)
    history = await repo.list_device_keys(name)
    assert len(history) == 1
    assert history[0]["id"] == issued["id"]
    assert history[0]["revoked_at"] is None
    assert await repo.resolve_device_key(name, hash_key(issued["device_key"])) == "key-device"


async def test_concurrent_replacement_keeps_exactly_one_active_key(postgres_container, apply_migrations):
    engine = create_async_engine(postgres_container)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    name = _unique_name("concurrent_keys")
    device_id = f"{name}-device"
    try:
        async with sessions() as session:
            session.add(Device(device_id=device_id, type_id="default", device_data={}))
            session.add(Extension(name=name, description="", schema_version=1, enabled=False))
            await session.commit()

        async def issue(key_hash):
            async with sessions() as session:
                return await SqlAlchemyExtensionRepository(session).create_device_key(
                    name, device_id, None, key_hash,
                )

        ids = await asyncio.gather(issue(hash_key("concurrent-a")), issue(hash_key("concurrent-b")))
        assert len(set(ids)) == 2
        async with sessions() as session:
            history = await SqlAlchemyExtensionRepository(session).list_device_keys(name)
            assert len(history) == 2
            assert sum(k["revoked_at"] is None for k in history) == 1
    finally:
        async with sessions() as session:
            await session.execute(delete(Extension).where(Extension.name == name))
            await session.execute(delete(Device).where(Device.device_id == device_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.parametrize("action,can_manage", [("extension.read", False), ("extension.register", True)])
async def test_key_history_and_uuid_revocation_permissions(client, db_session, fake_jwt_user, action, can_manage):
    name, _ = await _setup(client, db_session)
    issued = (await client.post(
        f"/extensions/{name}/device-keys", json={"device_id": "key-device"},
    )).json()
    oid = f"{name}-user"
    await AbacFixtures(db_session).setup(
        users={"key-manager": oid},
        roles={"key-role": [action]},
        teams={"key-team": {"scope": None, "roles": ["key-role"], "users": ["key-manager"]}},
    )
    fake_jwt_user.update(oid=oid, sub=oid, roles=[])
    response = await client.delete(f"/extensions/{name}/device-keys/by-id/{issued['id']}")
    assert response.status_code == (204 if can_manage else 403)
    response = await client.post(f"/extensions/{name}/device-keys", json={"device_id": "key-device"})
    assert response.status_code == (201 if can_manage else 403)
    response = await client.get(f"/extensions/{name}/device-keys")
    assert response.status_code == (403 if can_manage else 200)


@pytest.mark.parametrize("delete_owner", ["extension", "device"])
async def test_key_history_cascades_with_owner(client, db_session, delete_owner):
    name, _ = await _setup(client, db_session)
    for _ in range(2):
        await client.post(f"/extensions/{name}/device-keys", json={"device_id": "key-device"})
    if delete_owner == "extension":
        assert (await client.delete(f"/extensions/{name}")).status_code == 204
    else:
        device = (await db_session.execute(select(Device).where(Device.device_id == "key-device"))).scalar_one()
        await db_session.delete(device)
        await db_session.commit()
    assert (await db_session.execute(select(ExtensionDeviceKey))).scalars().all() == []
