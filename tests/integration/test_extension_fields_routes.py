"""`/extension-fields` routes: the fields extensions declare and the types they are applied to."""
import pytest

from db.registry import repo_registry
from db.repos.extension import ExtensionRepository
from routers.schemas import normalize_field_definition

from tests.integration.endpoint_fixtures import (
    _endpoint_repo,
    seed_endpoint,
    seed_endpoint_type,
    unique_label,
)
from tests.integration.extension_fixtures import STRING_FIELD, seed_extension
from tests.integration.test_abac_authorization import AbacFixtures

USER_OID = "extension-fields-perm-oid"


def _find(listing, name):
    return next((item for item in listing if item["extension"] == name), None)


async def _seed_managed_type(db_session, extension: str) -> str:
    created = await _endpoint_repo(db_session).create_endpoint_type(
        label=unique_label("et"),
        description=None,
        fields={"serial_number": STRING_FIELD},
        mapping={},
        managed_by={"serial_number": extension},
    )
    return created["type_id"]


class TestListExtensionFields:
    async def test_lists_only_extensions_with_fields(self, client, db_session):
        with_fields = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        without_fields = await seed_extension(db_session, {})

        response = await client.get("/extension-fields")
        assert response.status_code == 200, response.text
        listing = response.json()

        item = _find(listing, with_fields)
        assert item is not None
        assert item["enabled"] is True
        assert item["fields"]["serial_number"]["label"] == "Serial number"
        assert item["applied"] == {"serial_number": []}
        assert _find(listing, without_fields) is None

    async def test_applied_lists_types_managed_by_the_extension(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD, "batch": STRING_FIELD})
        type_id = await _seed_managed_type(db_session, name)
        await _seed_managed_type(db_session, "someone_else")

        response = await client.get("/extension-fields")
        assert response.status_code == 200, response.text
        item = _find(response.json(), name)
        assert item["applied"] == {"serial_number": [type_id], "batch": []}

    async def test_applied_type_ids_are_sorted(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_ids = [await _seed_managed_type(db_session, name) for _ in range(3)]

        response = await client.get("/extension-fields")
        item = _find(response.json(), name)
        assert item["applied"]["serial_number"] == sorted(type_ids)

    async def test_disabled_extension_listed_with_enabled_false(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD}, enabled=False)

        response = await client.get("/extension-fields")
        assert response.status_code == 200, response.text
        item = _find(response.json(), name)
        assert item is not None
        assert item["enabled"] is False

    async def test_ordered_by_extension_name(self, client, db_session):
        await seed_extension(db_session, {"serial_number": STRING_FIELD})
        await seed_extension(db_session, {"batch": STRING_FIELD})

        names = [item["extension"] for item in (await client.get("/extension-fields")).json()]
        assert names == sorted(names)


class TestExtensionFieldsReadPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": USER_OID,
            "sub": USER_OID,
            "preferred_username": "extension-fields-perm@test.com",
            "name": "Extension Fields Perm",
            "roles": [],
        }

    async def _grant(self, db_session, *actions):
        await AbacFixtures(db_session).setup(
            users={"tester": USER_OID},
            roles={"r": list(actions)},
            teams={"t": {"roles": ["r"], "users": ["tester"]}},
        )

    async def test_platform_config_read_can_list(self, client, db_session):
        await self._grant(db_session, "platform.config.read")
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})

        response = await client.get("/extension-fields")
        assert response.status_code == 200, response.text
        assert _find(response.json(), name) is not None

    async def test_endpoint_read_alone_cannot_list(self, client, db_session):
        await self._grant(db_session, "device.endpoint.read")
        assert (await client.get("/extension-fields")).status_code == 403


RICH_FIELD = normalize_field_definition(
    {
        "type": "string",
        "label": "Serial number",
        "description": "Printed on the rating plate",
        "ui": "select",
        "options": ["SN-A", "SN-B"],
        "validation": {"max_length": 20},
        "default": "SN-A",
        "changeable": False,
        "show_in_list": True,
    }
)


def _apply_url(extension: str) -> str:
    return f"/extension-fields/{extension}/apply"


class TestApplyExtensionFields:
    async def test_apply_copies_definition_and_marks_managed(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": RICH_FIELD})
        type_id = await seed_endpoint_type(db_session)

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "applied": [{"type_id": type_id, "key": "serial_number"}],
            "skipped": [],
        }

        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert et["managed_by"] == {"serial_number": name}

        # The stored copy equals the stored manifest definition, nested members included.
        db_session.expire_all()
        stored_row = await repo_registry.get(ExtensionRepository)(db_session).get_extension_row(name)
        stored_type = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert stored_type["fields"]["serial_number"] == stored_row["endpoint_fields"]["serial_number"]
        assert stored_type["fields"]["serial_number"] == RICH_FIELD

    async def test_apply_skips_existing_admin_key(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(
            db_session, fields={"serial_number": {"type": "string", "label": "Mine"}}
        )

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["applied"] == []
        assert body["skipped"] == [
            {"type_id": type_id, "key": "serial_number", "reason": "key_exists"}
        ]

        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert et["fields"]["serial_number"]["label"] == "Mine"
        assert et["managed_by"] == {}

    async def test_apply_twice_reports_key_exists(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        body = {"type_ids": [type_id], "keys": ["serial_number"]}

        assert (await client.post(_apply_url(name), json=body)).status_code == 200
        second = await client.post(_apply_url(name), json=body)
        assert second.status_code == 200, second.text
        assert second.json() == {
            "applied": [],
            "skipped": [{"type_id": type_id, "key": "serial_number", "reason": "key_exists"}],
        }
        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert et["managed_by"] == {"serial_number": name}

    async def test_apply_dedupes_ids_and_keys(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)

        response = await client.post(
            _apply_url(name),
            json={"type_ids": [type_id, type_id], "keys": ["serial_number", "serial_number"]},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "applied": [{"type_id": type_id, "key": "serial_number"}],
            "skipped": [],
        }

    async def test_apply_keeps_other_fields_and_mapping(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(
            db_session,
            fields={"host": {"type": "string", "label": "Host"}},
            mapping={"host": "ip"},
        )

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 200, response.text
        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert set(et["fields"]) == {"host", "serial_number"}
        assert et["mapping"] == {"host": "ip"}

    async def test_apply_disabled_extension_is_409(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD}, enabled=False)
        type_id = await seed_endpoint_type(db_session)

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 409, response.text
        assert response.json()["message"] == (
            f"Extension '{name}' is disabled; enable it to apply its fields"
        )
        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert "serial_number" not in et["fields"]

    async def test_apply_unknown_extension_type_or_key_is_404(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)

        unknown_ext = await client.post(
            _apply_url("no_such_extension"),
            json={"type_ids": [type_id], "keys": ["serial_number"]},
        )
        assert unknown_ext.status_code == 404, unknown_ext.text
        assert unknown_ext.json()["message"] == "Extension 'no_such_extension' not found"

        unknown_key = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number", "nope"]}
        )
        assert unknown_key.status_code == 404, unknown_key.text
        assert unknown_key.json()["message"] == f"Extension '{name}' declares no endpoint field 'nope'"

        unknown_type = await client.post(
            _apply_url(name),
            json={"type_ids": [type_id, "no-such-type"], "keys": ["serial_number"]},
        )
        assert unknown_type.status_code == 404, unknown_type.text
        assert unknown_type.json()["message"] == "EndpointType 'no-such-type' not found"

        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert "serial_number" not in et["fields"]
        assert et["managed_by"] == {}

    async def test_apply_empty_lists_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)

        for body in (
            {"type_ids": [], "keys": ["serial_number"]},
            {"type_ids": [type_id], "keys": []},
        ):
            response = await client.post(_apply_url(name), json=body)
            assert response.status_code == 422, response.text
            assert response.json()["message"] == "type_ids and keys must not be empty"

    async def test_existing_values_become_field_values(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        await AbacFixtures(db_session).setup(devices={"dev-1": {"region": "EU"}})
        endpoint_id = await seed_endpoint(db_session, "dev-1", type_id, {"serial_number": "SN-1"})

        before = (await client.get(f"/endpoints/{endpoint_id}")).json()
        assert before["endpoint_data"]["serial_number"]["field"] is None

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 200, response.text

        after = (await client.get(f"/endpoints/{endpoint_id}")).json()
        assert after["endpoint_data"]["serial_number"]["field"] is not None
        assert after["endpoint_data"]["serial_number"]["value"] == "SN-1"


class TestApplyPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": USER_OID,
            "sub": USER_OID,
            "preferred_username": "extension-fields-perm@test.com",
            "name": "Extension Fields Perm",
            "roles": [],
        }

    async def _grant(self, db_session, *actions):
        await AbacFixtures(db_session).setup(
            users={"tester": USER_OID},
            roles={"r": list(actions)},
            teams={"t": {"roles": ["r"], "users": ["tester"]}},
        )

    async def test_platform_config_write_can_apply(self, client, db_session):
        await self._grant(db_session, "platform.config.write")
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 200, response.text

    async def test_platform_config_read_cannot_apply(self, client, db_session):
        await self._grant(db_session, "platform.config.read")
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)

        response = await client.post(
            _apply_url(name), json={"type_ids": [type_id], "keys": ["serial_number"]}
        )
        assert response.status_code == 403, response.text
        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["managed_by"] == {}
