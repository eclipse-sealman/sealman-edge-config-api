"""Endpoint type CRUD with admin-defined fields and roles (spec §1.1, §1.2)."""
from uuid import uuid4

import pytest

from db.registry import repo_registry
from db.repos.endpoint import EndpointRepository

STRING_FIELD = {"type": "string", "label": "Text"}
INT_FIELD = {"type": "integer", "label": "Number"}
BOOL_FIELD = {"type": "boolean", "label": "Flag"}


def _label() -> str:
    return f"et-{uuid4().hex[:8]}"


async def _create(client, **body):
    response = await client.post("/endpoint-types", json={"label": _label(), **body})
    assert response.status_code == 201, response.text
    return response.json()


class TestEndpointTypeFields:
    async def test_create_with_no_fields_adds_nothing(self, client):
        body = await _create(client)
        assert body["fields"] == {}
        assert body["mapping"] == {}

    async def test_create_keeps_only_the_given_fields(self, client):
        body = await _create(client, fields={"serial": STRING_FIELD})
        assert set(body["fields"]) == {"serial"}
        assert body["mapping"] == {}

    async def test_ip_and_name_are_not_forced_required(self, client):
        body = await _create(
            client,
            fields={"ip": STRING_FIELD, "name": STRING_FIELD},
            mapping={"ip": "ip", "name": "name"},
        )
        assert body["fields"]["ip"]["required"] is False
        assert body["fields"]["name"]["required"] is False
        assert body["mapping"] == {"ip": "ip", "name": "name"}

    async def test_field_called_ip_may_be_integer_without_role(self, client):
        body = await _create(client, fields={"ip": INT_FIELD})
        assert body["fields"]["ip"]["type"] == "integer"

    async def test_patch_can_remove_ip_and_name(self, client):
        created = await _create(
            client,
            fields={"ip": STRING_FIELD, "name": STRING_FIELD},
            mapping={"ip": "ip", "name": "name"},
        )
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}", json={"fields": {"ip": None, "name": None}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"] == {}
        assert response.json()["mapping"] == {}

    async def test_removing_a_role_field_drops_only_its_role(self, client):
        created = await _create(
            client,
            fields={"ip": STRING_FIELD, "name": STRING_FIELD},
            mapping={"ip": "ip", "name": "name"},
        )
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}", json={"fields": {"name": None}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["mapping"] == {"ip": "ip"}

    async def test_patch_mapping_replaces_the_whole_mapping(self, client):
        created = await _create(
            client,
            fields={"addr": STRING_FIELD, "title": STRING_FIELD},
            mapping={"addr": "ip"},
        )
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}", json={"mapping": {"title": "name"}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["mapping"] == {"title": "name"}

    async def test_patch_without_mapping_keeps_the_mapping(self, client):
        created = await _create(client, fields={"addr": STRING_FIELD}, mapping={"addr": "ip"})
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}", json={"label": _label()}
        )
        assert response.status_code == 200, response.text
        assert response.json()["mapping"] == {"addr": "ip"}

    async def test_legacy_type_can_still_be_edited(self, client, db_session):
        # Shape of types created before this change: required ip/name, mapping {"ip": "ip"}.
        repo = repo_registry.get(EndpointRepository)(db_session)
        legacy = await repo.create_endpoint_type(
            label=_label(),
            description=None,
            fields={
                "ip": {"type": "string", "label": "IP Address", "required": True, "changeable": True, "ui": "input"},
                "name": {"type": "string", "label": "Name", "required": True, "changeable": True, "ui": "input", "default": "Unnamed"},
            },
            mapping={"ip": "ip"},
        )
        relabel = await client.patch(f"/endpoint-types/{legacy['type_id']}", json={"label": _label()})
        assert relabel.status_code == 200, relabel.text
        field_edit = await client.patch(
            f"/endpoint-types/{legacy['type_id']}", json={"fields": {"name": {"label": "Machine name"}}}
        )
        assert field_edit.status_code == 200, field_edit.text
        assert field_edit.json()["mapping"] == {"ip": "ip"}


class TestEndpointTypeRoleValidation:
    @pytest.mark.parametrize(
        "fields, mapping, message",
        [
            ({"addr": STRING_FIELD}, {"addr": "hostname"}, "Unknown role 'hostname'"),
            ({"addr": STRING_FIELD}, {"addr": "IP"}, "Unknown role 'IP'"),
            ({"addr": STRING_FIELD}, {"addr": " ip"}, "Unknown role ' ip'"),
            ({"p": INT_FIELD}, {"p": "port"}, "Unknown role 'port'"),
            ({"a": STRING_FIELD, "b": STRING_FIELD}, {"a": "ip", "b": "ip"}, "more than one field"),
            ({"a": STRING_FIELD}, {"nope": "ip"}, "not a field on this type"),
            ({"addr": INT_FIELD}, {"addr": "ip"}, "needs a field of type 'string'"),
            ({"flag": BOOL_FIELD}, {"flag": "name"}, "needs a field of type 'string'"),
        ],
    )
    async def test_invalid_mapping_on_create_is_422(self, client, fields, mapping, message):
        response = await client.post(
            "/endpoint-types", json={"label": _label(), "fields": fields, "mapping": mapping}
        )
        assert response.status_code == 422, response.text
        assert message in response.json()["message"]

    async def test_invalid_mapping_on_patch_is_422(self, client):
        created = await _create(client, fields={"addr": STRING_FIELD})
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}", json={"mapping": {"nope": "ip"}}
        )
        assert response.status_code == 422, response.text

    async def test_changing_the_type_of_a_role_field_is_422(self, client):
        created = await _create(client, fields={"addr": STRING_FIELD}, mapping={"addr": "ip"})
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}",
            json={"fields": {"addr": {"type": "integer", "ui": "number"}}},
        )
        assert response.status_code == 422, response.text
        assert "Role 'ip'" in response.json()["message"]

    async def test_mapping_sent_with_removal_of_its_field_is_422(self, client):
        # Q1 (resolved): a sent mapping is validated as sent.
        created = await _create(client, fields={"addr": STRING_FIELD}, mapping={"addr": "ip"})
        response = await client.patch(
            f"/endpoint-types/{created['type_id']}",
            json={"fields": {"addr": None}, "mapping": {"addr": "ip"}},
        )
        assert response.status_code == 422, response.text
