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
