"""Internal Core routes that let an extension read its own endpoint field values (port 8500)."""
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Optional
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.endpoint import Endpoint

from tests.integration.endpoint_fixtures import _endpoint_repo, seed_endpoint
from tests.integration.extension_fixtures import (
    STRING_FIELD,
    seed_keyed_extension,
    set_endpoint_created_at,
)
from tests.integration.test_abac_authorization import AbacFixtures

DEVICE = "core-api-dev"
OTHER_DEVICE = "core-api-dev-2"
NOTE_FIELD = {"type": "string", "label": "Note"}


@pytest.fixture
async def internal_client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Like `client`, but on the internal side app; only `get_db` is overridden."""
    from db.session import get_db
    from extensions.setup import internal_app

    async def _override_get_db():
        yield db_session

    internal_app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=internal_app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as ac:
        yield ac
    internal_app.dependency_overrides.clear()


def _label() -> str:
    return f"core-{uuid4().hex[:8]}"


def _at(day: int, month: int = 1) -> datetime:
    return datetime(2026, month, day, tzinfo=timezone.utc)


async def _type(db_session, fields: Dict, managed_by: Optional[Dict[str, str]] = None) -> str:
    created = await _endpoint_repo(db_session).create_endpoint_type(
        label=_label(), description=None, fields=fields, mapping={}, managed_by=managed_by or {}
    )
    return created["type_id"]


async def _devices(db_session, *device_ids: str) -> None:
    await AbacFixtures(db_session).setup(devices={d: {} for d in device_ids})


async def _get_device(internal_client, name: str, key: Optional[str], device_id: str = DEVICE):
    headers = {"X-Internal-Key": key} if key is not None else {}
    return await internal_client.get(
        f"/core/extensions/{name}/devices/{device_id}/endpoints", headers=headers
    )


class TestCoreAuth:
    async def test_missing_and_invalid_key_are_401(self, internal_client, db_session):
        name, _ = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})

        missing = await _get_device(internal_client, name, None)
        assert missing.status_code == 401, missing.text
        assert missing.json()["detail"] == "Missing X-Internal-Key"

        invalid = await _get_device(internal_client, name, "wrong")
        assert invalid.status_code == 401, invalid.text
        assert invalid.json()["detail"] == "Invalid X-Internal-Key"

    async def test_key_of_another_extension_is_401(self, internal_client, db_session):
        name_a, key_a = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        name_b, _ = await seed_keyed_extension(db_session, {"asset_tag": STRING_FIELD})

        response = await _get_device(internal_client, name_b, key_a)
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "Invalid X-Internal-Key"

    async def test_disabled_extension_is_403(self, internal_client, db_session):
        name, key = await seed_keyed_extension(
            db_session, {"serial_number": STRING_FIELD}, enabled=False
        )

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == f"Extension '{name}' is disabled"

    async def test_success_is_audited_as_extension(self, internal_client, db_session, monkeypatch):
        from helper import AuditTrail

        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        entries = []

        async def fake_log(user, access, method=None):
            entries.append((user, access))

        monkeypatch.setattr(AuditTrail, "log", fake_log)

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert entries and entries[0][0] == f"extension:{name}"

    async def test_not_served_on_public_app(self, client, db_session):
        response = await client.get("/core/extensions/x/devices/d/endpoints")
        assert response.status_code == 404, response.text

        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        headers = {"X-Internal-Key": key}
        for path in (
            f"/core/extensions/{name}/devices/{DEVICE}/endpoints",
            f"/core/extensions/{name}/endpoints",
        ):
            response = await client.get(path, headers=headers)
            assert response.status_code == 404, (path, response.text)


class TestPerDevice:
    async def test_only_types_with_own_managed_fields(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        own = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": name})
        plain = await _type(db_session, {"serial_number": STRING_FIELD})
        foreign = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": "other"})
        own_ep = await seed_endpoint(db_session, DEVICE, own, {"serial_number": "SN-1"})
        await seed_endpoint(db_session, DEVICE, plain, {"serial_number": "SN-2"})
        await seed_endpoint(db_session, DEVICE, foreign, {"serial_number": "SN-3"})

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert [e["endpoint_id"] for e in response.json()] == [own_ep]

    async def test_data_holds_only_own_managed_keys(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        type_id = await _type(
            db_session,
            {"serial_number": STRING_FIELD, "asset_tag": STRING_FIELD, "note": NOTE_FIELD},
            {"serial_number": name, "asset_tag": "other"},
        )
        await seed_endpoint(
            db_session, DEVICE, type_id, {"serial_number": "SN-1", "asset_tag": "AT-1", "note": "n"}
        )

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert [e["data"] for e in response.json()] == [{"serial_number": "SN-1"}]

    async def test_absent_value_is_omitted(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        with_default = {**STRING_FIELD, "default": "SN-DEFAULT"}
        type_id = await _type(
            db_session, {"serial_number": with_default, "note": NOTE_FIELD}, {"serial_number": name}
        )
        await seed_endpoint(db_session, DEVICE, type_id, {"note": "n"})

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert [e["data"] for e in response.json()] == [{}]

    async def test_endpoints_ordered_by_created_at_asc(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        type_id = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": name})
        late = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "late"})
        early = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "early"})
        tie_a = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "tie-a"})
        tie_b = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "tie-b"})
        await set_endpoint_created_at(db_session, late, _at(3))
        await set_endpoint_created_at(db_session, early, _at(1))
        await set_endpoint_created_at(db_session, tie_a, _at(2))
        await set_endpoint_created_at(db_session, tie_b, _at(2))

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert [e["endpoint_id"] for e in response.json()] == [early, *sorted([tie_a, tie_b]), late]

    async def test_timestamps_and_ids_present(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        type_id = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": name})
        endpoint_id = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "SN-1"})

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        [item] = response.json()
        assert item["endpoint_id"] == endpoint_id
        assert item["device_id"] == DEVICE
        assert item["type_id"] == type_id
        datetime.fromisoformat(item["created_at"])
        datetime.fromisoformat(item["updated_at"])
        assert set(item) == {"endpoint_id", "device_id", "type_id", "created_at", "updated_at", "data"}

    async def test_unknown_device_is_empty_list(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})

        response = await _get_device(internal_client, name, key, device_id="no-such-device")
        assert response.status_code == 200, response.text
        assert response.json() == []

    async def test_other_devices_excluded(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE, OTHER_DEVICE)
        type_id = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": name})
        mine = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "SN-1"})
        await seed_endpoint(db_session, OTHER_DEVICE, type_id, {"serial_number": "SN-2"})

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert [e["endpoint_id"] for e in response.json()] == [mine]

    async def test_released_field_hides_endpoint(self, client, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        created = await client.post(
            "/endpoint-types",
            json={"label": _label(), "extension_fields": {name: ["serial_number"]}},
        )
        assert created.status_code == 201, created.text
        type_id = created.json()["type_id"]
        endpoint_id = await seed_endpoint(db_session, DEVICE, type_id, {"serial_number": "SN-1"})
        assert len((await _get_device(internal_client, name, key)).json()) == 1

        released = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": None}}
        )
        assert released.status_code == 200, released.text

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert response.json() == []
        endpoint = await client.get(f"/endpoints/{endpoint_id}")
        assert endpoint.json()["endpoint_data"]["serial_number"] == {"value": "SN-1", "field": None}

    async def test_retyped_endpoint_leaves_scope(self, client, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        managed = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": name})
        plain = await _type(db_session, {"note": NOTE_FIELD})
        endpoint_id = await seed_endpoint(db_session, DEVICE, managed, {"serial_number": "SN-1"})
        assert len((await _get_device(internal_client, name, key)).json()) == 1

        retyped = await client.patch(f"/endpoints/{endpoint_id}", json={"type_id": plain})
        assert retyped.status_code == 200, retyped.text

        response = await _get_device(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert response.json() == []


async def _get_page(internal_client, name: str, key: Optional[str], **params):
    headers = {"X-Internal-Key": key} if key is not None else {}
    return await internal_client.get(
        f"/core/extensions/{name}/endpoints", headers=headers, params=params
    )


def _device_ids(body) -> list:
    return [d["device_id"] for d in body["devices"]]


class TestAcrossDevices:
    async def _own_type(self, db_session, name: str, fields: Optional[Dict] = None) -> str:
        fields = fields or {"serial_number": STRING_FIELD}
        return await _type(db_session, fields, {key: name for key in fields})

    async def _endpoint(self, db_session, device_id, type_id, data, created_at) -> str:
        endpoint_id = await seed_endpoint(db_session, device_id, type_id, data)
        await set_endpoint_created_at(db_session, endpoint_id, created_at)
        return endpoint_id

    async def test_devices_ordered_by_earliest_matching_endpoint_desc(
        self, internal_client, db_session
    ):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, "core-x-dev1", "core-x-dev2")
        type_id = await self._own_type(db_session, name)
        await self._endpoint(db_session, "core-x-dev1", type_id, {"serial_number": "a"}, _at(1, 1))
        await self._endpoint(db_session, "core-x-dev1", type_id, {"serial_number": "b"}, _at(1, 3))
        await self._endpoint(db_session, "core-x-dev2", type_id, {"serial_number": "c"}, _at(1, 2))

        response = await _get_page(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert _device_ids(response.json()) == ["core-x-dev2", "core-x-dev1"]

    async def test_equal_rank_ordered_by_device_id(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, "core-x-b", "core-x-a")
        type_id = await self._own_type(db_session, name)
        await self._endpoint(db_session, "core-x-b", type_id, {"serial_number": "b"}, _at(5))
        await self._endpoint(db_session, "core-x-a", type_id, {"serial_number": "a"}, _at(5))

        response = await _get_page(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert _device_ids(response.json()) == ["core-x-a", "core-x-b"]

    async def test_rank_uses_matching_endpoints_only(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, "core-x-dev2", "core-x-dev3")
        type_id = await self._own_type(db_session, name)
        await self._endpoint(db_session, "core-x-dev3", type_id, {"serial_number": ""}, _at(1, 1))
        await self._endpoint(db_session, "core-x-dev3", type_id, {"serial_number": "SN"}, _at(1, 3))
        await self._endpoint(db_session, "core-x-dev2", type_id, {"serial_number": "SN"}, _at(1, 2))

        response = await _get_page(internal_client, name, key, non_empty="serial_number")
        assert response.status_code == 200, response.text
        # Ranked by its 2026-03-01 endpoint, dev3 comes first; by the empty one it would be last.
        assert _device_ids(response.json()) == ["core-x-dev3", "core-x-dev2"]

    async def test_device_lists_only_matching_endpoints_asc(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        type_id = await self._own_type(db_session, name)
        late = await self._endpoint(db_session, DEVICE, type_id, {"serial_number": "late"}, _at(3))
        await self._endpoint(db_session, DEVICE, type_id, {}, _at(2))
        early = await self._endpoint(db_session, DEVICE, type_id, {"serial_number": "early"}, _at(1))

        response = await _get_page(internal_client, name, key, non_empty="serial_number")
        assert response.status_code == 200, response.text
        [device] = response.json()["devices"]
        assert device["device_id"] == DEVICE
        assert [e["endpoint_id"] for e in device["endpoints"]] == [early, late]
        assert [e["data"] for e in device["endpoints"]] == [
            {"serial_number": "early"},
            {"serial_number": "late"},
        ]

    async def test_non_empty_excludes_missing_null_and_empty_string(
        self, internal_client, db_session
    ):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE)
        type_id = await self._own_type(db_session, name)
        await self._endpoint(db_session, DEVICE, type_id, {}, _at(1))
        await self._endpoint(db_session, DEVICE, type_id, {"serial_number": None}, _at(2))
        await self._endpoint(db_session, DEVICE, type_id, {"serial_number": ""}, _at(3))
        filled = await self._endpoint(db_session, DEVICE, type_id, {"serial_number": "SN"}, _at(4))

        response = await _get_page(internal_client, name, key, non_empty="serial_number")
        assert response.status_code == 200, response.text
        body = response.json()
        assert [e["endpoint_id"] for d in body["devices"] for e in d["endpoints"]] == [filled]
        assert body["total_devices"] == 1

    async def test_non_empty_includes_false_and_zero(self, internal_client, db_session):
        fields = {
            "active": {"type": "boolean", "label": "Active"},
            "count": {"type": "integer", "label": "Count"},
        }
        name, key = await seed_keyed_extension(db_session, fields)
        await _devices(db_session, DEVICE)
        type_id = await self._own_type(db_session, name, fields)
        falsy = await self._endpoint(db_session, DEVICE, type_id, {"active": False}, _at(1))
        zero = await self._endpoint(db_session, DEVICE, type_id, {"count": 0}, _at(2))

        by_bool = await _get_page(internal_client, name, key, non_empty="active")
        assert by_bool.status_code == 200, by_bool.text
        assert [e["endpoint_id"] for d in by_bool.json()["devices"] for e in d["endpoints"]] == [
            falsy
        ]
        assert by_bool.json()["devices"][0]["endpoints"][0]["data"] == {"active": False}

        by_int = await _get_page(internal_client, name, key, non_empty="count")
        assert by_int.status_code == 200, by_int.text
        assert [e["endpoint_id"] for d in by_int.json()["devices"] for e in d["endpoints"]] == [zero]
        assert by_int.json()["devices"][0]["endpoints"][0]["data"] == {"count": 0}

    async def test_non_empty_ignores_unmanaged_values(self, internal_client, db_session):
        name, key = await seed_keyed_extension(
            db_session, {"serial_number": STRING_FIELD, "asset_tag": STRING_FIELD}
        )
        await _devices(db_session, DEVICE)
        # `inventory` manages only asset_tag here; serial_number is a Custom Field value.
        type_id = await _type(db_session, {"asset_tag": STRING_FIELD}, {"asset_tag": name})
        await self._endpoint(db_session, DEVICE, type_id, {"asset_tag": "AT"}, _at(1))
        await db_session.execute(
            update(Endpoint)
            .where(Endpoint.type_id == type_id)
            .values(endpoint_data={"asset_tag": "AT", "serial_number": "SN"})
        )
        await db_session.flush()

        response = await _get_page(internal_client, name, key, non_empty="serial_number")
        assert response.status_code == 200, response.text
        assert response.json()["devices"] == []
        assert response.json()["total_devices"] == 0

    async def test_non_empty_declared_but_unapplied_key_is_empty(self, internal_client, db_session):
        name, key = await seed_keyed_extension(
            db_session, {"serial_number": STRING_FIELD, "asset_tag": STRING_FIELD}
        )
        await _devices(db_session, DEVICE)
        type_id = await _type(db_session, {"serial_number": STRING_FIELD}, {"serial_number": name})
        await self._endpoint(db_session, DEVICE, type_id, {"serial_number": "SN"}, _at(1))

        response = await _get_page(internal_client, name, key, non_empty="asset_tag")
        assert response.status_code == 200, response.text
        assert response.json()["devices"] == []
        assert response.json()["total_devices"] == 0

    async def test_without_non_empty_lists_all_in_scope_devices(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE, OTHER_DEVICE)
        type_id = await self._own_type(db_session, name)
        empty = await self._endpoint(db_session, DEVICE, type_id, {}, _at(1))
        blank = await self._endpoint(db_session, OTHER_DEVICE, type_id, {"serial_number": ""}, _at(2))

        response = await _get_page(internal_client, name, key)
        assert response.status_code == 200, response.text
        body = response.json()
        assert _device_ids(body) == [OTHER_DEVICE, DEVICE]
        assert body["total_devices"] == 2
        assert [[e["endpoint_id"] for e in d["endpoints"]] for d in body["devices"]] == [
            [blank],
            [empty],
        ]
        assert [d["endpoints"][0]["data"] for d in body["devices"]] == [{"serial_number": ""}, {}]

    async def test_pagination(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, "core-x-dev1", "core-x-dev2", "core-x-dev3")
        type_id = await self._own_type(db_session, name)
        for month, device_id in enumerate(["core-x-dev1", "core-x-dev2", "core-x-dev3"], start=1):
            await self._endpoint(db_session, device_id, type_id, {"serial_number": "SN"}, _at(1, month))

        response = await _get_page(internal_client, name, key, page=2, page_size=1)
        assert response.status_code == 200, response.text
        body = response.json()
        assert _device_ids(body) == ["core-x-dev2"]
        assert body["page"] == 2
        assert body["page_size"] == 1
        assert body["total_devices"] == 3

        beyond = await _get_page(internal_client, name, key, page=4, page_size=1)
        assert beyond.status_code == 200, beyond.text
        assert beyond.json()["devices"] == []
        assert beyond.json()["total_devices"] == 3

    async def test_defaults_page_1_size_20(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})

        response = await _get_page(internal_client, name, key)
        assert response.status_code == 200, response.text
        assert response.json() == {"devices": [], "page": 1, "page_size": 20, "total_devices": 0}

    async def test_scope_rule_applies(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await _devices(db_session, DEVICE, OTHER_DEVICE)
        mixed = await _type(
            db_session,
            {"serial_number": STRING_FIELD, "asset_tag": STRING_FIELD, "note": NOTE_FIELD},
            {"serial_number": name, "asset_tag": "other"},
        )
        foreign = await _type(db_session, {"asset_tag": STRING_FIELD}, {"asset_tag": "other"})
        plain = await _type(db_session, {"note": NOTE_FIELD})
        mine = await self._endpoint(
            db_session,
            DEVICE,
            mixed,
            {"serial_number": "SN", "asset_tag": "AT", "note": "n"},
            _at(1),
        )
        await self._endpoint(db_session, OTHER_DEVICE, foreign, {"asset_tag": "AT"}, _at(2))
        await self._endpoint(db_session, OTHER_DEVICE, plain, {"note": "n"}, _at(3))

        response = await _get_page(internal_client, name, key)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_devices"] == 1
        [device] = body["devices"]
        assert device["device_id"] == DEVICE
        assert [(e["endpoint_id"], e["data"]) for e in device["endpoints"]] == [
            (mine, {"serial_number": "SN"})
        ]

    async def test_non_empty_foreign_or_unknown_key_is_422(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        await seed_keyed_extension(db_session, {"asset_tag": STRING_FIELD})

        for bad in ("asset_tag", "nope"):
            response = await _get_page(internal_client, name, key, non_empty=bad)
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == (
                f"'{bad}' is not an endpoint field of extension '{name}'"
            )

    async def test_page_limits_are_422(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})

        for params in (
            {"page": 0},
            {"page": 1_000_001},
            {"page": 10**18},
            {"page_size": 0},
            {"page_size": 101},
            {"page": "x"},
        ):
            response = await _get_page(internal_client, name, key, **params)
            assert response.status_code == 422, (params, response.text)

    async def test_largest_page_is_accepted(self, internal_client, db_session):
        name, key = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})

        response = await _get_page(internal_client, name, key, page=1_000_000, page_size=100)
        assert response.status_code == 200, response.text
        assert response.json()["devices"] == []

    async def test_auth_401_and_403(self, internal_client, db_session):
        name, _ = await seed_keyed_extension(db_session, {"serial_number": STRING_FIELD})
        disabled, disabled_key = await seed_keyed_extension(
            db_session, {"serial_number": STRING_FIELD}, enabled=False
        )

        missing = await _get_page(internal_client, name, None)
        assert missing.status_code == 401, missing.text
        assert missing.json()["detail"] == "Missing X-Internal-Key"

        off = await _get_page(internal_client, disabled, disabled_key)
        assert off.status_code == 403, off.text
        assert off.json()["detail"] == f"Extension '{disabled}' is disabled"
