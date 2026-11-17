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
