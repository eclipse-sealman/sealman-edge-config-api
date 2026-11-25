import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.models.device import Device
from db.models.device_type import DeviceType
from db.models.endpoint import Endpoint, EndpointType
from db.models.service import Service, ServiceType
from db.repos.device_search import DeviceSearchRepository, SearchFilter
from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from db.sqlalchemy.asset_defaults import backfill_asset_defaults
from exceptions import APIError, ValidationError
from tests.integration.test_abac_authorization import AbacFixtures, _repo


def field(default=None, kind="string", **attributes):
    return {"type": kind, "label": "Value", "default": default, **attributes}


async def setup_asset(client, db_session, kind, fields):
    device_id = f"defaults-{uuid4().hex[:8]}"
    await AbacFixtures(db_session).setup(devices={device_id: {}})
    parent = device_id
    if kind == "service":
        et = await client.post("/endpoint-types", json={"label": str(uuid4())})
        endpoint = await client.post(
            f"/endpoints?device_id={device_id}",
            json={"type_id": et.json()["type_id"], "endpoint_data": {}},
        )
        parent = endpoint.json()["endpoint_id"]
    response = await client.post(
        f"/{kind}-types", json={"label": str(uuid4()), "fields": fields}
    )
    assert response.status_code == 201, response.text
    return response.json()["type_id"], parent


async def create(client, kind, type_id, parent, data):
    parent_key = "device_id" if kind == "endpoint" else "endpoint_id"
    response = await client.post(
        f"/{kind}s?{parent_key}={parent}",
        json={"type_id": type_id, f"{kind}_data": data},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def raw(db_session, kind, asset_id):
    model = Endpoint if kind == "endpoint" else Service
    id_column = Endpoint.endpoint_id if kind == "endpoint" else Service.service_id
    row = (await db_session.execute(
        select(model).where(id_column == asset_id).execution_options(populate_existing=True)
    )).scalar_one()
    return deepcopy(getattr(row, f"{kind}_data"))


@pytest.mark.parametrize("kind", ["endpoint", "service"])
async def test_creation_persists_defaults_before_validation(client, db_session, kind):
    fields = {
        "text": field("default", required=True),
        "enabled": field(True, "boolean"), "count": field(7, "integer"),
        "optional": field("fallback"),
    }
    type_id, parent = await setup_asset(client, db_session, kind, fields)
    created = await create(client, kind, type_id, parent, {"enabled": False, "count": 0, "optional": None})
    assert await raw(db_session, kind, created[f"{kind}_id"]) == {
        "text": "default", "enabled": False, "count": 0, "optional": None,
    }
    assert created[f"{kind}_data"]["optional"]["value"] is None


@pytest.mark.parametrize("kind", ["endpoint", "service"])
async def test_first_default_backfills_but_changes_and_clears_do_not(client, db_session, kind):
    type_id, parent = await setup_asset(client, db_session, kind, {"value": field()})
    missing = await create(client, kind, type_id, parent, {})
    manual = await create(client, kind, type_id, parent, {"value": "manual"})
    null = await create(client, kind, type_id, parent, {"value": None})
    url = f"/{kind}-types/{type_id}"
    response = await client.patch(url, json={"fields": {"value": {"default": "first"}}})
    assert response.status_code == 200, response.text
    assert await raw(db_session, kind, missing[f"{kind}_id"]) == {"value": "first"}
    assert await raw(db_session, kind, manual[f"{kind}_id"]) == {"value": "manual"}
    assert await raw(db_session, kind, null[f"{kind}_id"]) == {"value": None}

    entity_url = f"/{kind}s/{missing[f'{kind}_id']}"
    cleared = await client.patch(entity_url, json={f"{kind}_data": {"value": None}})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()[f"{kind}_data"]["value"]["value"] is None
    changed = await client.patch(url, json={"fields": {"value": {"default": "second"}}})
    assert changed.status_code == 200, changed.text
    await client.patch(entity_url, json={f"{kind}_data": {"note": "unrelated"}})
    assert await raw(db_session, kind, missing[f"{kind}_id"]) == {"note": "unrelated"}
    future = await create(client, kind, type_id, parent, {})
    assert await raw(db_session, kind, future[f"{kind}_id"]) == {"value": "second"}
    removed = await client.patch(url, json={"fields": {"value": {"default": None}}})
    assert removed.status_code == 200, removed.text
    assert removed.json()["fields"]["value"]["default"] is None
    newest = await create(client, kind, type_id, parent, {})
    assert await raw(db_session, kind, newest[f"{kind}_id"]) == {}
    assert await raw(db_session, kind, future[f"{kind}_id"]) == {"value": "second"}


def equipment(columns):
    return {"type": "list", "label": "Equipment", "item_fields": columns}


async def test_list_defaults_initialize_only_new_rows_and_preserve_explicit_cells(client, db_session):
    definition = equipment({"unit": field("pcs"), "amount": field(1, "integer")})
    type_id, parent = await setup_asset(client, db_session, "endpoint", {"equipment": definition})
    created = await create(client, "endpoint", type_id, parent, {
        "equipment": [{}, {"unit": None, "amount": 0, "custom": {"nested": True}}],
    })
    endpoint_id = created["endpoint_id"]
    rows = (await raw(db_session, "endpoint", endpoint_id))["equipment"]
    assert rows[0]["unit"] == "pcs" and rows[0]["amount"] == 1
    assert rows[1]["unit"] is None and rows[1]["amount"] == 0
    ids = [row["id"] for row in rows]
    del rows[0]["unit"]
    response = await client.patch(f"/endpoints/{endpoint_id}", json={
        "endpoint_data": {"equipment": [rows[1], rows[0], {}]},
    })
    assert response.status_code == 200, response.text
    stored = (await raw(db_session, "endpoint", endpoint_id))["equipment"]
    assert [row["id"] for row in stored[:2]] == ids[::-1]
    assert "unit" not in stored[1]
    assert stored[0]["custom"] == {"nested": True}
    assert stored[0]["unit"] is None
    assert stored[2]["unit"] == "pcs"
    assert response.json()["endpoint_data"]["equipment"]["value"] == stored
    await client.patch(f"/endpoints/{endpoint_id}", json={"endpoint_data": {"note": "unrelated"}})
    assert (await raw(db_session, "endpoint", endpoint_id))["equipment"] == stored
    absent = await create(client, "endpoint", type_id, parent, {})
    assert await raw(db_session, "endpoint", absent["endpoint_id"]) == {}


async def test_new_column_and_first_column_default_backfill_existing_rows_only(client, db_session):
    type_id, parent = await setup_asset(
        client, db_session, "endpoint", {"equipment": equipment({"unit": field()})}
    )
    created = await create(client, "endpoint", type_id, parent, {
        "equipment": [{}, {"unit": None}, {"unit": "kg", "extra": 0}],
    })
    absent = await create(client, "endpoint", type_id, parent, {})
    before = (await raw(db_session, "endpoint", created["endpoint_id"]))["equipment"]
    url = f"/endpoint-types/{type_id}"
    changed = await client.patch(url, json={"fields": {"equipment": {"item_fields": {
        "unit": field("pcs"), "enabled": field(False, "boolean"),
    }}}})
    assert changed.status_code == 200, changed.text
    rows = (await raw(db_session, "endpoint", created["endpoint_id"]))["equipment"]
    assert rows == [
        {**before[0], "unit": "pcs", "enabled": False},
        {**before[1], "enabled": False}, {**before[2], "enabled": False},
    ]
    assert await raw(db_session, "endpoint", absent["endpoint_id"]) == {}
    changed = await client.patch(url, json={"fields": {"equipment": {"item_fields": {
        "unit": field("litres"),
    }}}})
    assert changed.status_code == 200, changed.text
    assert (await raw(db_session, "endpoint", created["endpoint_id"]))["equipment"] == rows
    future = await create(client, "endpoint", type_id, parent, {"equipment": [{}]})
    assert future["endpoint_data"]["equipment"]["value"][0]["unit"] == "litres"


async def test_retype_initializes_missing_defaults_but_respects_clears_and_null_cells(client, db_session):
    old_type, parent = await setup_asset(client, db_session, "endpoint", {})
    created = await create(client, "endpoint", old_type, parent, {
        "cleared": "old", "equipment": [{"id": "retained", "unit": None, "custom": True}],
    })
    new_type = await client.post("/endpoint-types", json={
        "label": str(uuid4()), "fields": {
            "new": field("initialized", required=True), "cleared": field("fallback"),
            "equipment": equipment({"unit": field("pcs"), "amount": field(2, "integer")}),
        },
    })
    response = await client.patch(f"/endpoints/{created['endpoint_id']}", json={
        "type_id": new_type.json()["type_id"], "endpoint_data": {"cleared": None},
    })
    assert response.status_code == 200, response.text
    assert await raw(db_session, "endpoint", created["endpoint_id"]) == {
        "new": "initialized", "equipment": [
            {"id": "retained", "unit": None, "amount": 2, "custom": True},
        ],
    }


@pytest.mark.parametrize("kind", ["endpoint", "service"])
async def test_invalid_type_patch_and_backfill_failure_are_atomic(client, db_session, kind, monkeypatch):
    type_id, parent = await setup_asset(client, db_session, kind, {"value": field()})
    created = await create(client, kind, type_id, parent, {})
    url = f"/{kind}-types/{type_id}"
    invalid = await client.patch(url, json={"fields": {"value": {"default": 123}}})
    assert invalid.status_code == 422, invalid.text
    assert await raw(db_session, kind, created[f"{kind}_id"]) == {}

    module = __import__(f"db.sqlalchemy.{kind}", fromlist=["backfill_asset_defaults"])
    original = module.backfill_asset_defaults

    async def fail(session, model, fields_by_type):
        await original(session, model, fields_by_type)
        raise ValidationError("Simulated backfill failure", 422)

    monkeypatch.setattr(module, "backfill_asset_defaults", fail)
    repo_type = EndpointRepository if kind == "endpoint" else ServiceRepository
    with pytest.raises(ValidationError, match="Simulated backfill"):
        await getattr(_repo(repo_type, db_session), f"update_{kind}_type")(
            type_id, fields={"value": {"default": "first"}}
        )
    assert await raw(db_session, kind, created[f"{kind}_id"]) == {}
    stored_type = await client.get(url)
    assert stored_type.json()["fields"]["value"]["default"] is None


async def test_asset_backfill_script_is_dry_run_scoped_and_idempotent(client, db_session, monkeypatch):
    from scripts import backfill_asset_defaults as script

    type_id, parent = await setup_asset(client, db_session, "endpoint", {
        "value": field("first"), "equipment": equipment({"unit": field("pcs")}),
    })
    legacy = Endpoint(type_id=type_id, device_id=parent, endpoint_data={
        "equipment": [{"id": "legacy", "unknown": "keep"}, {"id": "null", "unit": None}],
    })
    db_session.add(legacy)
    await db_session.commit()
    endpoint_id = legacy.endpoint_id
    original = await raw(db_session, "endpoint", endpoint_id)
    monkeypatch.setattr(script, "AsyncSessionLocal", async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    ))
    assert await script.run(False, "endpoints", type_id) == {"endpoints": 2}
    assert await raw(db_session, "endpoint", endpoint_id) == original
    assert await script.run(True, "endpoints", type_id) == {"endpoints": 2}
    assert await script.run(True, "endpoints", type_id) == {"endpoints": 0}
    stored = await raw(db_session, "endpoint", endpoint_id)
    assert stored == {
        "value": "first", "equipment": [
            {"id": "legacy", "unknown": "keep", "unit": "pcs"}, {"id": "null", "unit": None},
        ],
    }
    with pytest.raises(APIError, match="Unknown endpoints type"):
        await script.run(True, "endpoints", str(uuid4()))


async def test_malformed_list_backfill_identifies_entity_and_rolls_back(client, db_session):
    from db.merge import newly_defaulted_fields

    type_id, parent = await setup_asset(client, db_session, "endpoint", {
        "equipment": equipment({"unit": field("pcs")}),
    })
    legacy = Endpoint(type_id=type_id, device_id=parent, endpoint_data={"equipment": ["invalid"]})
    db_session.add(legacy)
    await db_session.commit()
    definition = await db_session.get(EndpointType, type_id)
    with pytest.raises(ValidationError, match="must be an object; backfill aborted"):
        async with db_session.begin_nested():
            await backfill_asset_defaults(
                db_session, Endpoint, {type_id: newly_defaulted_fields({}, definition.fields)}
            )
    assert await raw(db_session, "endpoint", legacy.endpoint_id) == {"equipment": ["invalid"]}


@pytest.mark.parametrize("kind", ["endpoint", "service"])
async def test_search_and_reads_do_not_invent_unstored_defaults(client, db_session, kind):
    term = f"DEFAULT-{uuid4().hex}"
    type_id, parent = await setup_asset(client, db_session, kind, {"value": field(term)})
    defaulted = await create(client, kind, type_id, parent, {})
    null = await create(client, kind, type_id, parent, {"value": None})
    model = Endpoint if kind == "endpoint" else Service
    parent_data = {"device_id": parent} if kind == "endpoint" else {"endpoint_id": parent}
    legacy = model(type_id=type_id, **parent_data, **{f"{kind}_data": {}})
    db_session.add(legacy)
    await db_session.commit()
    legacy_id = getattr(legacy, f"{kind}_id")
    read = await client.get(f"/{kind}s/{legacy_id}")
    assert read.status_code == 200, read.text
    assert read.json()[f"{kind}_data"]["value"]["value"] is None
    repo = _repo(DeviceSearchRepository, db_session)
    matches = await getattr(repo, f"search_{kind}s")([term])
    assert [row[f"{kind}_id"] for row in matches] == [defaulted[f"{kind}_id"]]
    removed = await client.patch(
        f"/{kind}s/{defaulted[f'{kind}_id']}", json={f"{kind}_data": {"value": None}}
    )
    assert removed.status_code == 200, removed.text
    assert await getattr(repo, f"search_{kind}s")([term]) == []
    assert await repo.suggest_values(kind, "value", term, 10) == []
    assert await repo.find_device_ids([SearchFilter(kind, term, "value")], []) == []
    assert (await client.get(f"/{kind}s/{null[f'{kind}_id']}")).json()[
        f"{kind}_data"
    ]["value"]["value"] is None


async def test_service_backfill_script_preserves_nulls(client, db_session, monkeypatch):
    from scripts import backfill_asset_defaults as script

    type_id, parent = await setup_asset(client, db_session, "service", {"port": field(4840, "integer")})
    legacy = Service(type_id=type_id, endpoint_id=parent, service_data={})
    explicit_null = Service(type_id=type_id, endpoint_id=parent, service_data={"port": None})
    db_session.add_all([legacy, explicit_null])
    await db_session.commit()
    service_id, null_id = legacy.service_id, explicit_null.service_id
    monkeypatch.setattr(script, "AsyncSessionLocal", async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    ))
    assert await script.run(False, "services", type_id) == {"services": 1}
    assert await raw(db_session, "service", service_id) == {}
    assert await script.run(True, "services", type_id) == {"services": 1}
    assert await raw(db_session, "service", service_id) == {"port": 4840}
    assert await raw(db_session, "service", null_id) == {"port": None}


async def test_new_list_row_defaults_satisfy_required_columns(client, db_session):
    type_id, parent = await setup_asset(client, db_session, "endpoint", {
        "equipment": equipment({"unit": field("pcs", required=True)}),
    })
    created = await create(client, "endpoint", type_id, parent, {"equipment": [{}]})
    row = created["endpoint_data"]["equipment"]["value"][0]
    assert row["unit"] == "pcs"
    response = await client.patch(f"/endpoints/{created['endpoint_id']}", json={
        "endpoint_data": {"equipment": [row, {}]},
    })
    assert response.status_code == 200, response.text
    assert response.json()["endpoint_data"]["equipment"]["value"][1]["unit"] == "pcs"
    response = await client.patch(f"/endpoints/{created['endpoint_id']}", json={
        "endpoint_data": {"equipment": [{"id": row["id"], "unit": None}]},
    })
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("apply", [False, True])
async def test_all_asset_backfill_rolls_back_if_service_data_is_malformed(
    client, db_session, monkeypatch, apply,
):
    from scripts import backfill_asset_defaults as script

    endpoint_type, device_id = await setup_asset(
        client, db_session, "endpoint", {"value": field("endpoint-default")}
    )
    service_type, endpoint_id = await setup_asset(
        client, db_session, "service", {"value": field("service-default")}
    )
    endpoint = Endpoint(type_id=endpoint_type, device_id=device_id, endpoint_data={})
    service = Service(type_id=service_type, endpoint_id=endpoint_id, service_data="malformed")
    db_session.add_all([endpoint, service])
    await db_session.commit()
    legacy_id = endpoint.endpoint_id
    monkeypatch.setattr(script, "AsyncSessionLocal", async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    ))
    with pytest.raises(ValidationError, match="non-object data"):
        await script.run(apply)
    assert await raw(db_session, "endpoint", legacy_id) == {}


@pytest.mark.parametrize("kind", ["endpoint", "service"])
async def test_concurrent_asset_type_edit_revalidates_current_schema(
    postgres_container, apply_migrations, monkeypatch, kind,
):
    from db.sqlalchemy.endpoint import SqlAlchemyEndpointRepository
    from db.sqlalchemy.service import SqlAlchemyServiceRepository
    from routers.endpoint.router import update_endpoint_type
    from routers.endpoint.schemas import EndpointTypeUpdate
    from routers.service.router import update_service_type
    from routers.service.schemas import ServiceTypeUpdate

    if kind == "endpoint":
        model, repository = EndpointType, SqlAlchemyEndpointRepository
        route, schema = update_endpoint_type, EndpointTypeUpdate
    else:
        model, repository = ServiceType, SqlAlchemyServiceRepository
        route, schema = update_service_type, ServiceTypeUpdate
    engine = create_async_engine(postgres_container)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    type_id = f"concurrent-{uuid4()}"
    device_id = f"{type_id}-device"
    endpoint_id = str(uuid4())
    service_id = str(uuid4())
    entered, resume = asyncio.Event(), asyncio.Event()
    task = None
    try:
        async with factory() as session:
            session.add(DeviceType(type_id=type_id, label=type_id, fields={}))
            session.add(EndpointType(
                type_id=type_id, label=type_id,
                fields={"counter": field()} if kind == "endpoint" else {},
            ))
            await session.commit()
            session.add(Device(device_id=device_id, type_id=type_id, device_data={}))
            await session.commit()
            session.add(Endpoint(
                endpoint_id=endpoint_id, device_id=device_id, type_id=type_id, endpoint_data={},
            ))
            if kind == "service":
                session.add(ServiceType(
                    type_id=type_id, label=type_id, fields={"counter": field()},
                ))
            await session.commit()
            if kind == "service":
                session.add(Service(
                    service_id=service_id, endpoint_id=endpoint_id, type_id=type_id, service_data={},
                ))
                await session.commit()

        async with factory() as first, factory() as second:
            repo = repository(first)
            method = f"update_{kind}_type"
            original = getattr(repo, method)

            async def paused(*args, **kwargs):
                entered.set()
                await resume.wait()
                return await original(*args, **kwargs)

            monkeypatch.setattr(repo, method, paused)
            task = asyncio.create_task(route(
                type_id, schema(fields={"counter": {"default": "EU"}, "marker": field("pending")}),
                repo, None,
            ))
            await asyncio.wait_for(entered.wait(), timeout=10)
            await route(
                type_id,
                schema(fields={"counter": {"type": "integer", "ui": "number", "default": 7}}),
                repository(second), None,
            )
            resume.set()
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(task, timeout=10)
            assert exc.value.status_code == 422
            assert "counter" in exc.value.detail

        async with factory() as verify:
            stored = (await verify.execute(
                select(model.fields).where(model.type_id == type_id)
            )).scalar_one()
            assert stored["counter"]["type"] == "integer"
            assert stored["counter"]["default"] == 7
            assert "marker" not in stored
            assert await raw(
                verify, kind, endpoint_id if kind == "endpoint" else service_id,
            ) == {"counter": 7}
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with factory() as cleanup:
            await cleanup.execute(delete(Device).where(Device.device_id == device_id))
            await cleanup.execute(delete(ServiceType).where(ServiceType.type_id == type_id))
            await cleanup.execute(delete(EndpointType).where(EndpointType.type_id == type_id))
            await cleanup.execute(delete(DeviceType).where(DeviceType.type_id == type_id))
            await cleanup.commit()
        await engine.dispose()
