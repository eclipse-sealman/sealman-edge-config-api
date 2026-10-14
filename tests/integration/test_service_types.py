"""Service type CRUD with admin-defined fields and roles (spec §1.1, §1.2)."""
from uuid import uuid4

import pytest

STRING_FIELD = {"type": "string", "label": "Text"}
INT_FIELD = {"type": "integer", "label": "Number"}


def _label() -> str:
    return f"st-{uuid4().hex[:8]}"


async def _create(client, **body):
    response = await client.post("/service-types", json={"label": _label(), **body})
    assert response.status_code == 201, response.text
    return response.json()


class TestServiceTypeFields:
    async def test_create_with_no_fields_adds_nothing(self, client):
        body = await _create(client)
        assert body["fields"] == {}
        assert body["mapping"] == {}

    async def test_port_is_not_forced_required(self, client):
        body = await _create(client, fields={"port": INT_FIELD}, mapping={"port": "port"})
        assert body["fields"]["port"]["required"] is False
        assert body["mapping"] == {"port": "port"}

    async def test_field_called_port_may_be_a_string_without_role(self, client):
        body = await _create(client, fields={"port": STRING_FIELD})
        assert body["fields"]["port"]["type"] == "string"

    async def test_patch_can_remove_port_and_its_role(self, client):
        created = await _create(client, fields={"port": INT_FIELD}, mapping={"port": "port"})
        response = await client.patch(
            f"/service-types/{created['type_id']}", json={"fields": {"port": None}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"] == {}
        assert response.json()["mapping"] == {}

    async def test_patch_mapping_replaces_the_whole_mapping(self, client):
        created = await _create(
            client, fields={"a": INT_FIELD, "b": INT_FIELD}, mapping={"a": "port"}
        )
        response = await client.patch(
            f"/service-types/{created['type_id']}", json={"mapping": {"b": "port"}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["mapping"] == {"b": "port"}

    async def test_port_role_default_is_added_to_default_scan_ports(self, client):
        await _create(
            client,
            fields={"svc_port": {**INT_FIELD, "default": 47101}},
            mapping={"svc_port": "port"},
        )
        ports = (await client.get("/network/default-scan-ports")).json()
        assert 47101 in ports

    async def test_field_called_port_without_role_is_not_a_default_scan_port(self, client):
        # Q5 (resolved): only the port-role field's default counts.
        await _create(client, fields={"port": {**INT_FIELD, "default": 47102}})
        ports = (await client.get("/network/default-scan-ports")).json()
        assert 47102 not in ports


class TestServiceTypeRoleValidation:
    @pytest.mark.parametrize(
        "fields, mapping, message",
        [
            ({"p": INT_FIELD}, {"p": "listener"}, "Unknown role 'listener'"),
            ({"p": INT_FIELD}, {"p": "PORT"}, "Unknown role 'PORT'"),
            ({"addr": STRING_FIELD}, {"addr": "ip"}, "Unknown role 'ip'"),
            ({"a": INT_FIELD, "b": INT_FIELD}, {"a": "port", "b": "port"}, "more than one field"),
            ({"a": INT_FIELD}, {"nope": "port"}, "not a field on this type"),
            ({"p": STRING_FIELD}, {"p": "port"}, "needs a field of type 'integer'"),
        ],
    )
    async def test_invalid_mapping_on_create_is_422(self, client, fields, mapping, message):
        response = await client.post(
            "/service-types", json={"label": _label(), "fields": fields, "mapping": mapping}
        )
        assert response.status_code == 422, response.text
        assert message in response.json()["message"]

    async def test_invalid_mapping_on_patch_is_422(self, client):
        created = await _create(client, fields={"p": STRING_FIELD})
        response = await client.patch(
            f"/service-types/{created['type_id']}", json={"mapping": {"p": "port"}}
        )
        assert response.status_code == 422, response.text


class TestServiceTypeBrowserKind:
    async def test_patch_without_browser_kind_keeps_it(self, client):
        created = await _create(client, browser_kind="http")
        url = f"/service-types/{created['type_id']}"
        for body in ({"label": _label()}, {"mapping": {}}):
            response = await client.patch(url, json=body)
            assert response.status_code == 200, response.text
            assert response.json()["browser_kind"] == "http"
        assert (await client.get(url)).json()["browser_kind"] == "http"

    async def test_patch_with_null_browser_kind_clears_it(self, client):
        created = await _create(client, browser_kind="http")
        response = await client.patch(
            f"/service-types/{created['type_id']}", json={"browser_kind": None}
        )
        assert response.status_code == 200, response.text
        assert response.json()["browser_kind"] is None

    async def test_patch_with_browser_kind_sets_it(self, client):
        created = await _create(client)
        assert created["browser_kind"] is None
        response = await client.patch(
            f"/service-types/{created['type_id']}", json={"browser_kind": "ftp"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["browser_kind"] == "ftp"


class TestServiceTypeRoleOnFieldRemoval:
    async def test_patch_mapping_referencing_field_removed_in_same_request_is_422(self, client):
        created = await _create(client, fields={"p": INT_FIELD}, mapping={"p": "port"})
        url = f"/service-types/{created['type_id']}"
        response = await client.patch(url, json={"fields": {"p": None}, "mapping": {"p": "port"}})
        assert response.status_code == 422, response.text
        unchanged = (await client.get(url)).json()
        assert unchanged["fields"].keys() == {"p"}
        assert unchanged["mapping"] == {"p": "port"}

    async def test_fields_only_patch_drops_only_removed_fields_role(self, client):
        created = await _create(
            client, fields={"p": INT_FIELD, "q": INT_FIELD}, mapping={"p": "port"}
        )
        response = await client.patch(
            f"/service-types/{created['type_id']}", json={"fields": {"p": None}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"].keys() == {"q"}
        assert response.json()["mapping"] == {}
