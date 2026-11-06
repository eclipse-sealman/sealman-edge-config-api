import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.models.device import Device
from db.models.device_type import DeviceType
from db.repos.device import DeviceRepository
from db.repos.device_type import DeviceTypeRepository
from db.sqlalchemy.device_defaults import backfill_device_defaults, effective_type_fields
from db.sqlalchemy.device_type import SqlAlchemyDeviceTypeRepository
from exceptions import ValidationError
from tests.integration.test_abac_authorization import AbacFixtures, _repo


def field(default=None, kind="string", required=False):
    definition = {"type": kind, "label": "Test field", "required": required}
    if default is not None:
        definition["default"] = default
    return definition


async def seed(db_session, fields=None, type_id="default", devices=None):
    if type_id == "default":
        dt = await db_session.get(DeviceType, type_id)
        dt.fields = fields or {}
    else:
        db_session.add(DeviceType(type_id=type_id, label=type_id, fields=fields or {}))
    await db_session.flush()
    for device_id, metadata in (devices or {}).items():
        db_session.add(Device(device_id=device_id, type_id=type_id, device_data=metadata))
    await db_session.commit()


async def raw(db_session, device_id):
    return await _repo(DeviceRepository, db_session).get_device_meta_raw(device_id)


async def test_creation_persists_effective_defaults_without_overwriting_values(db_session):
    await seed(db_session, {"region": field("EU"), "enabled": field(False, "boolean")})
    await seed(db_session, {"region": field("US"), "count": field(0, "integer")}, "special")
    repo = _repo(DeviceRepository, db_session)
    await repo.create_device("defaulted", {}, "special")
    await repo.create_device("explicit", {"region": "APAC", "enabled": True, "count": 9}, "special")
    await repo.create_device("null-value", {"region": None}, "special")

    assert await raw(db_session, "defaulted") == {"region": "US", "enabled": False, "count": 0}
    assert await raw(db_session, "explicit") == {"region": "APAC", "enabled": True, "count": 9}
    assert (await raw(db_session, "null-value"))["region"] is None


async def test_required_default_is_materialized_before_validation(db_session):
    await seed(db_session, {"region": field("EU", required=True)})
    await _repo(DeviceRepository, db_session).create_device("required-default", {})
    assert await raw(db_session, "required-default") == {"region": "EU"}


@pytest.mark.parametrize("existing_field", [False, True])
async def test_first_default_backfills_only_missing_values(client, db_session, existing_field):
    await seed(
        db_session, {"region": field()} if existing_field else {},
        devices={"missing": {}, "manual": {"region": "US"}, "null": {"region": None}},
    )
    response = await client.patch("/device-types/default", json={"fields": {"region": field("EU")}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "missing") == {"region": "EU"}
    assert await raw(db_session, "manual") == {"region": "US"}
    assert await raw(db_session, "null") == {"region": None}


async def test_default_change_affects_only_future_devices(client, db_session):
    await seed(db_session, {"region": field("EU")}, devices={"stored": {"region": "EU"}, "missing": {}})
    response = await client.patch("/device-types/default", json={"fields": {"region": {"default": "US"}}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "stored") == {"region": "EU"}
    assert await raw(db_session, "missing") == {}
    repo = _repo(DeviceRepository, db_session)
    assert (await repo.get_device_metadata("missing"))["device_metadata"]["region"]["value"] is None
    assert (await repo.get_device_joined_snapshot("stored"))["device_metadata"]["region"]["value"] == "EU"
    await repo.create_device("new", {})
    assert await raw(db_session, "new") == {"region": "US"}


async def test_default_type_backfill_respects_type_overrides(client, db_session):
    await seed(db_session, devices={"base": {}})
    await seed(db_session, {}, "inheriting", {"inherited": {}})
    await seed(db_session, {"region": field("US")}, "overriding", {"override": {}})
    response = await client.patch("/device-types/default", json={"fields": {"region": field("EU")}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "base") == {"region": "EU"}
    assert await raw(db_session, "inherited") == {"region": "EU"}
    # This type already had a default; an unrelated default-type edit must not reseed it.
    assert await raw(db_session, "override") == {}
    await _repo(DeviceRepository, db_session).create_device("new-override", {}, "overriding")
    assert await raw(db_session, "new-override") == {"region": "US"}


async def test_type_specific_default_does_not_backfill_other_types(client, db_session):
    await seed(db_session, {"region": field()}, devices={"base": {}})
    await seed(db_session, {}, "special", {"target": {}})
    response = await client.patch("/device-types/special", json={"fields": {"region": {"default": "US"}}})
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["region"]["type"] == "string"
    assert await raw(db_session, "target") == {"region": "US"}
    assert await raw(db_session, "base") == {}


async def test_removing_default_or_field_preserves_device_values(client, db_session):
    await seed(db_session, {"region": field("EU")}, devices={"stored": {"region": "EU"}})
    response = await client.patch("/device-types/default", json={"fields": {"region": {"default": None}}})
    assert response.status_code == 200, response.text
    assert response.json()["fields"]["region"]["default"] is None
    repo = _repo(DeviceRepository, db_session)
    await repo.create_device("without-default", {})
    assert await raw(db_session, "without-default") == {}
    response = await client.patch("/device-types/default", json={"fields": {"region": None}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "stored") == {"region": "EU"}
    assert (await repo.get_device_metadata("stored"))["device_metadata"]["region"] == {"value": "EU", "field": None}


async def test_clearing_optional_value_does_not_reapply_default(db_session):
    await seed(db_session, {"region": field("EU")}, devices={"stored": {"region": "US"}})
    result = await _repo(DeviceRepository, db_session).update_device_metadata("stored", {"region": None})
    assert await raw(db_session, "stored") == {}
    assert result["device_metadata"]["region"]["value"] is None


async def test_invalid_type_edit_does_not_backfill(client, db_session):
    await seed(db_session, devices={"missing": {}})
    response = await client.patch("/device-types/default", json={"fields": {"region": field(7)}})
    assert response.status_code == 422, response.text
    assert await raw(db_session, "missing") == {}


async def test_backfill_and_schema_change_roll_back_together(db_session, monkeypatch):
    import db.sqlalchemy.device_type as module

    await seed(db_session, devices={"missing": {}})
    original = module.backfill_device_defaults
    async def fail_after_backfill(session, fields_by_type):
        await original(session, fields_by_type)
        raise RuntimeError("Simulated backfill failure")

    monkeypatch.setattr(module, "backfill_device_defaults", fail_after_backfill)
    with pytest.raises(RuntimeError, match="Simulated backfill failure"):
        async with db_session.begin_nested():
            await _repo(DeviceTypeRepository, db_session).update_device_type(
                "default", fields={"region": field("EU")}
            )
    assert await raw(db_session, "missing") == {}
    dt = await db_session.get(DeviceType, "default")
    assert dt.fields == {}


async def test_cutover_backfill_is_idempotent_and_respects_effective_types(db_session):
    await seed(db_session, {"region": field("EU")}, devices={"base": {}, "manual": {"region": "APAC"}})
    await seed(db_session, {"region": field("US")}, "special", {"custom": {}})
    types = list((await db_session.execute(select(DeviceType))).scalars().all())
    fields = effective_type_fields(types)
    assert await backfill_device_defaults(db_session, fields) == 2
    assert await backfill_device_defaults(db_session, fields) == 0
    assert await raw(db_session, "base") == {"region": "EU"}
    assert await raw(db_session, "custom") == {"region": "US"}
    assert await raw(db_session, "manual") == {"region": "APAC"}


async def test_scoped_list_detail_permissions_and_meta_values_agree(client, db_session, fake_jwt_user):
    from authorization.permission_types import Device as Actions

    await seed(db_session)
    await AbacFixtures(db_session).setup(
        users={"reader": fake_jwt_user["oid"]},
        scopes={"eu": {"attr": {"region": "EU"}}},
        roles={"reader": [Actions.READ]},
        teams={"eu": {"scope": "eu", "roles": ["reader"], "users": ["reader"]}},
    )
    db_session.add_all([
        Device(device_id="missing", type_id="default", device_data={}),
        Device(device_id="outside", type_id="default", device_data={"region": "US"}),
    ])
    await db_session.commit()
    response = await client.patch("/device-types/default", json={"fields": {"region": field("EU")}})
    assert response.status_code == 200, response.text
    fake_jwt_user["roles"] = []

    listed = await client.get("/devices")
    assert listed.status_code == 200, listed.text
    assert [d["deviceId"] for d in listed.json()] == ["missing"]
    detail = await client.get("/missing/metadata")
    assert detail.status_code == 200, detail.text
    assert detail.json()["deviceMetadata"]["region"]["value"] == "EU"
    permissions = await client.get("/auth/permissions/device", params={"device_id": "missing"})
    assert permissions.status_code == 200, permissions.text
    assert Actions.READ in permissions.json()["permissions"]
    values = await client.get("/devices/meta-values")
    assert values.status_code == 200, values.text
    assert values.json()["region"] == ["EU"]
    assert (await client.get("/outside/metadata")).status_code == 403


@pytest.mark.parametrize("region,expected", [(None, 200), ("US", 403)])
async def test_creation_authorizes_defaults_that_will_be_stored(
    client, db_session, fake_jwt_user, monkeypatch, region, expected
):
    import routers.devices.router as router

    await seed(db_session, {"region": field("EU")})
    await AbacFixtures(db_session).setup(
        users={"creator": fake_jwt_user["oid"]},
        scopes={"eu": {"attr": {"region": "EU"}}},
        roles={"creator": ["device.create"]},
        teams={"eu": {"scope": "eu", "roles": ["creator"], "users": ["creator"]}},
    )
    fake_jwt_user["roles"] = []

    async def create_locally(device_id, body, repo, template_repo):
        return await repo.create_device(device_id, body.get("meta", {}), body.get("type_id", "default"))

    create = AsyncMock(side_effect=create_locally)
    monkeypatch.setattr(router, "create_device", create)
    response = await client.put("/devices/new", json={"meta": {} if region is None else {"region": region}})
    assert response.status_code == expected, response.text
    if expected == 200:
        assert await raw(db_session, "new") == {"region": "EU"}
    else:
        create.assert_not_awaited()


async def test_backfill_preserves_false_zero_and_empty_values(client, db_session):
    await seed(
        db_session, devices={
            "missing": {},
            "explicit": {"enabled": False, "count": 0, "text": ""},
        },
    )
    response = await client.patch("/device-types/default", json={"fields": {
        "enabled": field(True, "boolean"), "count": field(5, "integer"), "text": field("default"),
    }})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "missing") == {"enabled": True, "count": 5, "text": "default"}
    assert await raw(db_session, "explicit") == {"enabled": False, "count": 0, "text": ""}


async def test_unrelated_type_edit_does_not_backfill_existing_defaults(client, db_session):
    await seed(db_session, {"region": field("EU")}, devices={"missing": {}})
    response = await client.patch("/device-types/default", json={"fields": {"region": {"label": "Region"}}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "missing") == {}


async def test_script_dry_run_and_explicit_apply(db_session, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from scripts import backfill_device_defaults as script

    await seed(db_session, {"region": field("EU")}, devices={"legacy": {}})
    monkeypatch.setattr(script, "AsyncSessionLocal", async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    ))
    assert await script.run(apply=False) == 1
    assert await raw(db_session, "legacy") == {}
    assert await script.run(apply=True) == 1
    assert await raw(db_session, "legacy") == {"region": "EU"}
    assert await script.run(apply=True) == 0


async def test_concurrent_type_edit_revalidates_before_backfill_and_commit(
    postgres_container, apply_migrations,
):
    from routers.device_type.router import update_device_type
    from routers.device_type.schemas import DeviceTypeUpdate

    engine = create_async_engine(postgres_container)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    type_id = f"concurrent-{uuid4()}"
    device_id = f"{type_id}-device"
    entered = asyncio.Event()
    resume = asyncio.Event()
    task = None

    class PausedRepository(SqlAlchemyDeviceTypeRepository):
        async def update_device_type(self, *args, **kwargs):
            entered.set()
            await resume.wait()
            return await super().update_device_type(*args, **kwargs)

    try:
        async with factory() as session:
            session.add(DeviceType(type_id=type_id, label=type_id, fields={"counter": field()}))
            await session.commit()
            session.add(Device(device_id=device_id, type_id=type_id, device_data={}))
            await session.commit()

        async with factory() as first, factory() as second:
            task = asyncio.create_task(update_device_type(
                type_id,
                DeviceTypeUpdate(fields={"counter": {"default": "EU"}, "marker": field("uncommitted")}),
                PausedRepository(first),
                None,
            ))
            await asyncio.wait_for(entered.wait(), timeout=10)
            await update_device_type(
                type_id,
                DeviceTypeUpdate(fields={"counter": {"type": "integer", "ui": "number", "default": 7}}),
                SqlAlchemyDeviceTypeRepository(second),
                None,
            )
            resume.set()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=10)
            assert exc.value.status_code == 422
            assert "counter" in exc.value.detail

        async with factory() as verify:
            stored_fields = (await verify.execute(
                select(DeviceType.fields).where(DeviceType.type_id == type_id)
            )).scalar_one()
            assert stored_fields["counter"]["type"] == "integer"
            assert stored_fields["counter"]["default"] == 7
            assert "marker" not in stored_fields
            assert (await verify.execute(
                select(Device.device_data).where(Device.device_id == device_id)
            )).scalar_one() == {"counter": 7}
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with factory() as cleanup:
            await cleanup.execute(delete(Device).where(Device.device_id == device_id))
            await cleanup.execute(delete(DeviceType).where(DeviceType.type_id == type_id))
            await cleanup.commit()
        await engine.dispose()


@pytest.mark.parametrize("metadata", [None, [], ["region"], "legacy", 0, False])
@pytest.mark.parametrize("apply", [False, True])
async def test_backfill_script_rejects_non_object_metadata_without_partial_changes(
    db_session, monkeypatch, metadata, apply,
):
    from scripts import backfill_device_defaults as script

    await seed(db_session, {"region": field("EU")}, devices={"valid": {}})
    await seed(db_session, {"region": field("US")}, "special", {"invalid": metadata})
    monkeypatch.setattr(script, "AsyncSessionLocal", async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    ))
    with pytest.raises(ValidationError, match="invalid.*non-object metadata") as exc:
        await script.run(apply=apply)
    assert exc.value.status_code == 422
    assert await raw(db_session, "valid") == {}
    assert (await db_session.execute(
        select(Device.device_data).where(Device.device_id == "invalid")
    )).scalar_one() == metadata


async def test_type_edit_rolls_back_schema_and_backfill_on_non_object_metadata(client, db_session):
    await seed(db_session, devices={"valid": {}, "invalid": None})
    response = await client.patch("/device-types/default", json={"fields": {"region": field("EU")}})
    assert response.status_code == 422, response.text
    assert "non-object metadata" in response.text
    assert await raw(db_session, "valid") == {}
    assert (await db_session.execute(
        select(Device.device_data).where(Device.device_id == "invalid")
    )).scalar_one() is None
    dt = await db_session.get(DeviceType, "default")
    assert dt.fields == {}


async def test_type_backfill_does_not_reject_unaffected_malformed_devices(client, db_session):
    await seed(db_session, devices={"unaffected": None})
    await seed(db_session, {"region": field()}, "special", {"target": {}})
    response = await client.patch("/device-types/special", json={"fields": {"region": {"default": "US"}}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, "target") == {"region": "US"}
    assert (await db_session.execute(
        select(Device.device_data).where(Device.device_id == "unaffected")
    )).scalar_one() is None
