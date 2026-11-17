"""List field definition rules (repeatable rows of scalar sub-fields).

Schema violations answer 400 (main.py maps RequestValidationError to 400); role checks answer 422.
"""
from uuid import uuid4

import pytest

IDENT = {"type": "string", "label": "Identifier", "required": True}
EQUIPMENT = {"type": "list", "label": "Equipment", "required": True, "item_fields": {"identifier": IDENT}}


def _label() -> str:
    return f"et-{uuid4().hex[:8]}"


async def _create(client, **body):
    response = await client.post("/endpoint-types", json={"label": _label(), **body})
    assert response.status_code == 201, response.text
    return response.json()


class TestListDefinition:
    async def test_list_field_is_accepted_on_an_endpoint_type(self, client):
        body = await _create(client, fields={"equipment": EQUIPMENT})
        field = body["fields"]["equipment"]
        assert field["type"] == "list"
        assert set(field["item_fields"]) == {"identifier"}
        assert field["ui"] is None

    @pytest.mark.parametrize(
        "field",
        [
            {"type": "list", "label": "L"},
            {"type": "list", "label": "L", "item_fields": {}},
            {"type": "list", "label": "L", "item_fields": {"nested": {"type": "list", "label": "N", "item_fields": {"a": IDENT}}}},
            {"type": "list", "label": "L", "item_fields": {"id": IDENT}},
            {**EQUIPMENT, "validation": {"min_length": 1}},
            {**EQUIPMENT, "options": ["a"]},
            {**EQUIPMENT, "default": []},
            {**EQUIPMENT, "ui": "input"},
            {**EQUIPMENT, "changeable": False},
            {**EQUIPMENT, "item_order": ["identifier"]},
            {"type": "string", "label": "S", "item_fields": {"identifier": IDENT}},
        ],
    )
    async def test_invalid_list_definitions_are_rejected(self, client, field):
        response = await client.post(
            "/endpoint-types", json={"label": _label(), "fields": {"equipment": field}}
        )
        assert response.status_code == 400, response.text

    async def test_mapping_onto_a_list_field_is_rejected(self, client):
        response = await client.post(
            "/endpoint-types",
            json={"label": _label(), "fields": {"equipment": EQUIPMENT}, "mapping": {"equipment": "name"}},
        )
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("path", ["/device-types", "/service-types"])
    async def test_list_field_is_rejected_on_device_and_service_types(self, client, path):
        response = await client.post(path, json={"label": _label(), "fields": {"equipment": EQUIPMENT}})
        assert response.status_code == 400
        assert "endpoint types" in response.text


class TestListTypePatch:
    async def test_item_order_follows_the_order_sent(self, client):
        body = await _create(
            client,
            fields={"equipment": {**EQUIPMENT, "item_fields": {"zeta": IDENT, "alpha": IDENT}}},
        )
        assert body["fields"]["equipment"]["item_order"] == ["zeta", "alpha"]

    async def test_patch_adds_a_sub_field_and_keeps_the_others(self, client):
        type_id = (await _create(client, fields={"equipment": EQUIPMENT}))["type_id"]
        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={"fields": {"equipment": {"item_fields": {"crm_id": {"type": "string", "label": "CRM id"}}}}},
        )
        assert response.status_code == 200, response.text
        field = response.json()["fields"]["equipment"]
        assert set(field["item_fields"]) == {"identifier", "crm_id"}
        assert field["item_order"] == ["identifier", "crm_id"]
        assert field["label"] == "Equipment"

        # item_order is not a client input: schema-level rejection (400)
        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"equipment": {"item_order": ["crm_id"]}}}
        )
        assert response.status_code == 400, response.text

    async def test_patch_accepts_a_definition_echoed_back(self, client):
        created = await _create(
            client, fields={"serial": {"type": "string", "label": "Serial"}, "equipment": EQUIPMENT}
        )
        type_id = created["type_id"]
        serial = created["fields"]["serial"]
        response = await client.patch(f"/endpoint-types/{type_id}", json={"fields": {"serial": serial}})
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["serial"] == serial
        assert response.json()["fields"]["equipment"]["item_order"] == ["identifier"]

        echoed = {k: v for k, v in created["fields"]["equipment"].items() if k != "item_order"}
        response = await client.patch(f"/endpoint-types/{type_id}", json={"fields": {"equipment": echoed}})
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["equipment"]["item_order"] == ["identifier"]

        device = await client.post(
            "/device-types", json={"label": _label(), "fields": {"site": {"type": "string", "label": "Site"}}}
        )
        assert device.status_code == 201, device.text
        site = device.json()["fields"]["site"]
        response = await client.patch(
            f"/device-types/{device.json()['type_id']}", json={"fields": {"site": site}}
        )
        assert response.status_code == 200, response.text

    async def test_patch_null_removes_a_sub_field(self, client):
        type_id = (await _create(client, fields={"equipment": EQUIPMENT}))["type_id"]
        await client.patch(
            f"/endpoint-types/{type_id}",
            json={"fields": {"equipment": {"item_fields": {"crm_id": {"type": "string", "label": "CRM id"}}}}},
        )
        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"equipment": {"item_fields": {"identifier": None}}}}
        )
        assert response.status_code == 200, response.text
        field = response.json()["fields"]["equipment"]
        assert field["item_order"] == ["crm_id"]
        assert set(field["item_fields"]) == {"crm_id"}

    async def test_patch_removing_the_last_sub_field_is_rejected(self, client):
        type_id = (await _create(client, fields={"equipment": EQUIPMENT}))["type_id"]
        # raised in the router's merged-definition check (422)
        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"equipment": {"item_fields": {"identifier": None}}}}
        )
        assert response.status_code == 422, response.text
        stored = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert stored["fields"]["equipment"]["item_order"] == ["identifier"]

    async def test_patch_can_turn_a_string_field_into_a_list_and_back(self, client):
        type_id = (await _create(client, fields={"serial": {"type": "string", "label": "Serial"}}))["type_id"]
        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={"fields": {"serial": {"type": "list", "item_fields": {"identifier": IDENT}}}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["serial"]["ui"] is None

        response = await client.patch(f"/endpoint-types/{type_id}", json={"fields": {"serial": {"type": "string"}}})
        assert response.status_code == 200, response.text
        field = response.json()["fields"]["serial"]
        assert field["item_fields"] is None
        assert field["ui"] == "input"


DEVICE = "list-dev"


async def _endpoint_type(client, equipment=EQUIPMENT, **extra_fields) -> str:
    return (await _create(client, fields={"equipment": equipment, **extra_fields}))["type_id"]


async def _post_endpoint(client, type_id, endpoint_data):
    return await client.post(
        f"/endpoints?device_id={DEVICE}", json={"type_id": type_id, "endpoint_data": endpoint_data}
    )


def _items(body):
    return body["endpoint_data"]["equipment"]["value"]


@pytest.fixture
async def device(db_session):
    from tests.integration.test_abac_authorization import AbacFixtures

    await AbacFixtures(db_session).setup(devices={DEVICE: {}})


@pytest.mark.usefixtures("device")
class TestListValues:
    async def test_required_list_needs_one_item(self, client):
        type_id = await _endpoint_type(client)
        assert (await _post_endpoint(client, type_id, {})).status_code == 422
        assert (await _post_endpoint(client, type_id, {"equipment": []})).status_code == 422

    async def test_items_are_validated_per_sub_field(self, client):
        type_id = await _endpoint_type(client)
        for value in ([{"identifier": 5}], [{}], "SEP-00412"):
            response = await _post_endpoint(client, type_id, {"equipment": value})
            assert response.status_code == 422, (value, response.text)

    async def test_ids_are_generated_kept_and_unknown_ids_rejected(self, client):
        type_id = await _endpoint_type(client)
        created = await _post_endpoint(
            client, type_id, {"equipment": [{"identifier": "SEP-00412"}, {"identifier": "SEP-00977"}]}
        )
        assert created.status_code == 201, created.text
        items = _items(created.json())
        assert [i["identifier"] for i in items] == ["SEP-00412", "SEP-00977"]
        assert items[0]["id"] and items[1]["id"] and items[0]["id"] != items[1]["id"]

        endpoint_id = created.json()["endpoint_id"]
        patched = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"endpoint_data": {"equipment": [*items, {"identifier": "SEP-01000"}]}},
        )
        assert patched.status_code == 200, patched.text
        new_items = _items(patched.json())
        assert [i["id"] for i in new_items[:2]] == [i["id"] for i in items]
        assert new_items[2]["id"]

        unknown = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"endpoint_data": {"equipment": [{"id": "nope", "identifier": "X"}]}},
        )
        assert unknown.status_code == 422, unknown.text

        non_string = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"endpoint_data": {"equipment": [{"id": [1], "identifier": "X"}]}},
        )
        assert non_string.status_code == 422, non_string.text

        twice = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"endpoint_data": {"equipment": [new_items[0], new_items[0]]}},
        )
        assert twice.status_code == 422, twice.text

    async def test_empty_list_is_stored_as_absent(self, client):
        type_id = await _endpoint_type(client, {**EQUIPMENT, "required": False})
        created = await _post_endpoint(client, type_id, {"equipment": [{"identifier": "SEP-00412"}]})
        assert created.status_code == 201, created.text
        response = await client.patch(
            f"/endpoints/{created.json()['endpoint_id']}", json={"endpoint_data": {"equipment": []}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["endpoint_data"]["equipment"]["value"] is None

    async def test_unknown_item_keys_survive_a_save(self, client):
        type_id = await _endpoint_type(client)
        created = await _post_endpoint(
            client, type_id, {"equipment": [{"identifier": "SEP-00412", "crm_id": "4711"}]}
        )
        assert created.status_code == 201, created.text
        items = _items(created.json())
        items[0]["identifier"] = "SEP-00413"
        response = await client.patch(
            f"/endpoints/{created.json()['endpoint_id']}", json={"endpoint_data": {"equipment": items}}
        )
        assert response.status_code == 200, response.text
        assert _items(response.json())[0]["crm_id"] == "4711"

    async def test_sub_field_default_is_applied_on_read(self, client):
        unit = {"type": "string", "label": "Unit", "default": "pcs"}
        type_id = await _endpoint_type(
            client, {**EQUIPMENT, "item_fields": {"identifier": IDENT, "unit": unit}}
        )
        created = await _post_endpoint(client, type_id, {"equipment": [{"identifier": "SEP-00412"}]})
        assert created.status_code == 201, created.text
        assert _items(created.json())[0]["unit"] == "pcs"

    async def test_scalar_stored_under_a_list_key_reads_and_rejects_cleanly(self, client):
        type_id = await _endpoint_type(client, {"type": "string", "label": "Equipment"})
        created = await _post_endpoint(client, type_id, {"equipment": "SEP-1"})
        assert created.status_code == 201, created.text
        endpoint_id = created.json()["endpoint_id"]

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"equipment": EQUIPMENT}}
        )
        assert response.status_code == 200, response.text

        read = await client.get(f"/endpoints/{endpoint_id}")
        assert read.status_code == 200, read.text
        assert _items(read.json()) == "SEP-1"

        rejected = await client.patch(f"/endpoints/{endpoint_id}", json={"endpoint_data": {"note": "x"}})
        assert rejected.status_code == 422, rejected.text

        fixed = await client.patch(
            f"/endpoints/{endpoint_id}",
            json={"endpoint_data": {"equipment": [{"identifier": "SEP-1"}]}},
        )
        assert fixed.status_code == 200, fixed.text
