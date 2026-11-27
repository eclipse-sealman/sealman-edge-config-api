import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from db.base import Base
from db.models.device import Device
from db.models.device_type import DeviceType
from scripts import remove_undefined_device_fields as script
from tests.integration.test_device_defaults import field, raw, seed


@pytest.fixture
async def db_session(postgres_container):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(conn, tables=[
                    DeviceType.__table__, Device.__table__,
                ])
            )
        async with engine.connect() as connection:
            transaction = await connection.begin()
            async with AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
            ) as session:
                if await session.get(DeviceType, "default") is None:
                    session.add(DeviceType(type_id="default", label="Default", fields={}))
                    await session.commit()
                try:
                    yield session
                finally:
                    await session.close()
                    await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def cleanup(db_session, monkeypatch):
    monkeypatch.setattr(script, "AsyncSessionLocal", async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    ))
    return script.run


async def test_selected_fields_only_remove_undefined_keys(db_session, cleanup):
    await seed(db_session, {"region": field("EU")}, devices={
        "base": {"region": "EU", "legacy": None, "other": 0},
    })
    await seed(db_session, {"legacy": field()}, "special", {
        "custom": {"region": "US", "legacy": "keep", "other": False},
    })
    result = await cleanup(["region", "legacy"], all_fields=False, apply=True, all_device_types=True)
    assert result.devices == 1
    assert result.fields == {"legacy": 1}
    assert await raw(db_session, "base") == {"region": "EU", "other": 0}
    assert await raw(db_session, "custom") == {"region": "US", "legacy": "keep", "other": False}


async def test_all_preserves_default_and_type_fields_and_is_idempotent(db_session, cleanup):
    await seed(db_session, {"region": field("EU")}, devices={
        "base": {"region": None, "legacy": False},
    })
    await seed(db_session, {"count": field(0, "integer")}, "special", {
        "custom": {"region": "EU", "count": 0, "legacy": "", "other": None},
    })
    result = await cleanup([], all_fields=True, apply=True, all_device_types=True)
    assert result.devices == 2
    assert result.fields == {"legacy": 2, "other": 1}
    assert await raw(db_session, "base") == {"region": None}
    assert await raw(db_session, "custom") == {"region": "EU", "count": 0}
    repeated = await cleanup([], all_fields=True, apply=True, all_device_types=True)
    assert repeated.devices == 0
    assert repeated.fields == {}


async def test_dry_run_does_not_persist_changes(db_session, cleanup):
    await seed(db_session, {"region": field()}, devices={
        "base": {"region": "EU", "legacy": "old"},
    })
    result = await cleanup(["legacy"], all_fields=False, apply=False, all_device_types=True)
    assert result.fields == {"legacy": 1}
    assert await raw(db_session, "base") == {"region": "EU", "legacy": "old"}


async def test_special_field_names_are_literal(db_session, cleanup):
    names = ["with space", "quote'field", 'double"quote', "nested.path", "--all", "unicode-\u00e9"]
    await seed(db_session, {"region": field()}, devices={
        "base": {"region": "EU", **dict.fromkeys(names, "old")},
    })
    result = await cleanup(names, all_fields=False, apply=True, all_device_types=True)
    assert result.fields == dict.fromkeys(sorted(names), 1)
    assert await raw(db_session, "base") == {"region": "EU"}


@pytest.mark.parametrize("fields,all_fields", [([], False), (["legacy"], True)])
async def test_invalid_selection_fails_before_database_access(cleanup, fields, all_fields):
    with pytest.raises(ValueError, match="exclusively"):
        await cleanup(fields, all_fields, apply=True, all_device_types=True)


@pytest.mark.parametrize("all_fields", [False, True])
@pytest.mark.parametrize("apply", [False, True])
async def test_type_filter_only_changes_selected_devices(db_session, cleanup, all_fields, apply):
    await seed(db_session, {"region": field("EU")}, devices={
        "base": {"region": "EU", "legacy": "base", "other": "base"},
    })
    await seed(db_session, {"count": field(0, "integer")}, "special", {
        "custom": {"region": "US", "count": 0, "legacy": "custom", "other": "custom"},
    })
    result = await cleanup(
        [] if all_fields else ["legacy", "region", "count"],
        all_fields=all_fields, apply=apply, device_type="special",
    )
    assert result.devices == 1
    assert result.fields == ({"legacy": 1, "other": 1} if all_fields else {"legacy": 1})
    assert await raw(db_session, "base") == {"region": "EU", "legacy": "base", "other": "base"}
    expected = {"region": "US", "count": 0}
    if not apply:
        expected.update({"legacy": "custom", "other": "custom"})
    elif not all_fields:
        expected["other"] = "custom"
    assert await raw(db_session, "custom") == expected


async def test_unknown_type_is_an_error_not_a_silent_noop(db_session, cleanup):
    await seed(db_session, devices={"base": {"legacy": "keep"}})
    with pytest.raises(ValueError, match="not found"):
        await cleanup(["legacy"], all_fields=False, apply=True, device_type="unknown")
    assert await raw(db_session, "base") == {"legacy": "keep"}


async def test_known_type_without_devices_reports_zero(db_session, cleanup):
    await seed(db_session, {}, "empty")
    result = await cleanup(["legacy"], all_fields=False, apply=True, device_type="empty")
    assert result.devices == 0
    assert result.fields == {}


@pytest.mark.parametrize("device_type,all_types", [(None, False), ("default", True)])
async def test_type_selection_is_required_and_exclusive(cleanup, device_type, all_types):
    with pytest.raises(ValueError, match="exclusively"):
        await cleanup(
            ["legacy"], all_fields=False, apply=True,
            device_type=device_type, all_device_types=all_types,
        )
