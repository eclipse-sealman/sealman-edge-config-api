"""Endpoint types carry a `managed_by` marker ({field_key: extension}) for extension fields."""
from uuid import uuid4

import pytest

from db.registry import repo_registry
from db.repos.endpoint import EndpointRepository
from db.repos.extension import ExtensionRepository

from tests.integration.endpoint_fixtures import _endpoint_repo, seed_endpoint_type
from tests.integration.extension_fixtures import STRING_FIELD, seed_extension
from tests.integration.test_abac_authorization import AbacFixtures

SERIAL_FIELD = {
    "type": "string",
    "label": "Serial",
    "required": False,
    "ui": "input",
    "changeable": True,
    "show_in_list": True,
}


def _label() -> str:
    return f"et-{uuid4().hex[:8]}"


class TestManagedByMarker:
    async def test_new_type_has_empty_managed_by(self, client):
        created = await client.post("/endpoint-types", json={"label": _label()})
        assert created.status_code == 201, created.text
        assert created.json()["managed_by"] == {}

        response = await client.get(f"/endpoint-types/{created.json()['type_id']}")
        assert response.status_code == 200, response.text
        assert response.json()["managed_by"] == {}

    async def test_repo_create_stores_managed_by(self, client, db_session):
        repo = repo_registry.get(EndpointRepository)(db_session)
        created = await repo.create_endpoint_type(
            label=_label(),
            description=None,
            fields={"serial_number": SERIAL_FIELD},
            mapping={},
            managed_by={"serial_number": "inventory"},
        )
        assert created["managed_by"] == {"serial_number": "inventory"}

        response = await client.get(f"/endpoint-types/{created['type_id']}")
        assert response.status_code == 200, response.text
        assert response.json()["managed_by"] == {"serial_number": "inventory"}

    async def test_type_list_includes_managed_by(self, client):
        created = await client.post("/endpoint-types", json={"label": _label()})
        assert created.status_code == 201, created.text

        response = await client.get("/endpoint-types")
        assert response.status_code == 200, response.text
        listed = {t["type_id"]: t for t in response.json()}
        assert listed[created.json()["type_id"]]["managed_by"] == {}

    async def test_patch_ignores_sent_managed_by(self, client):
        created = await client.post("/endpoint-types", json={"label": _label()})
        assert created.status_code == 201, created.text

        response = await client.patch(
            f"/endpoint-types/{created.json()['type_id']}",
            json={"label": _label(), "managed_by": {"x": "y"}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["managed_by"] == {}


# --- extension_fields on type create and type PATCH (D23, D24) -------------------------------

ROLE_MESSAGE = "'serial_number' is managed by extension '{}' and cannot have a role"
NOTE_FIELD = {"type": "string", "label": "Note"}


async def _assert_stored_copy(db_session, type_id: str, extension: str, key: str) -> None:
    """The type's stored definition equals the extension's stored manifest definition."""
    db_session.expire_all()
    row = await repo_registry.get(ExtensionRepository)(db_session).get_extension_row(extension)
    stored_type = await _endpoint_repo(db_session).get_endpoint_type(type_id)
    assert stored_type["fields"][key] == row["endpoint_fields"][key]


async def _labels(client) -> set:
    return {t["label"] for t in (await client.get("/endpoint-types")).json()}


class TestCreateWithExtensionFields:
    async def test_create_copies_selected_fields_as_managed(self, client, db_session):
        name = await seed_extension(
            db_session, {"serial_number": STRING_FIELD, "batch": STRING_FIELD}
        )
        response = await client.post(
            "/endpoint-types",
            json={
                "label": _label(),
                "fields": {"note": NOTE_FIELD},
                "extension_fields": {name: ["serial_number"]},
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        await _assert_stored_copy(db_session, body["type_id"], name, "serial_number")
        assert "batch" not in body["fields"]
        assert body["fields"]["note"]["label"] == "Note"
        assert body["managed_by"] == {"serial_number": name}

    async def test_create_role_on_extension_field_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        label = _label()
        response = await client.post(
            "/endpoint-types",
            json={
                "label": label,
                "extension_fields": {name: ["serial_number"]},
                "mapping": {"serial_number": "name"},
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == ROLE_MESSAGE.format(name)
        assert label not in await _labels(client)

    async def test_create_clash_with_fields_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        label = _label()
        response = await client.post(
            "/endpoint-types",
            json={
                "label": label,
                "fields": {"serial_number": NOTE_FIELD},
                "extension_fields": {name: ["serial_number"]},
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == "'serial_number' is in both fields and extension_fields"
        assert label not in await _labels(client)

    async def test_create_disabled_extension_is_409(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD}, enabled=False)
        label = _label()
        response = await client.post(
            "/endpoint-types",
            json={"label": label, "extension_fields": {name: ["serial_number"]}},
        )
        assert response.status_code == 409, response.text
        assert response.json()["message"] == (
            f"Extension '{name}' is disabled; enable it to apply its fields"
        )
        assert label not in await _labels(client)

    async def test_create_unknown_extension_or_key_is_404(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        label = _label()

        unknown_ext = await client.post(
            "/endpoint-types",
            json={"label": label, "extension_fields": {"missing_ext": ["serial_number"]}},
        )
        assert unknown_ext.status_code == 404, unknown_ext.text
        assert unknown_ext.json()["message"] == "Extension 'missing_ext' not found"

        unknown_key = await client.post(
            "/endpoint-types",
            json={"label": label, "extension_fields": {name: ["nope"]}},
        )
        assert unknown_key.status_code == 404, unknown_key.text
        assert unknown_key.json()["message"] == f"Extension '{name}' declares no endpoint field 'nope'"
        assert label not in await _labels(client)

    async def test_create_without_extension_fields_unchanged(self, client):
        response = await client.post(
            "/endpoint-types", json={"label": _label(), "fields": {"note": NOTE_FIELD}}
        )
        assert response.status_code == 201, response.text
        assert response.json()["managed_by"] == {}
        assert set(response.json()["fields"]) == {"note"}


class TestPatchWithExtensionFields:
    async def test_patch_adds_extension_fields_with_field_edits_in_one_request(
        self, client, db_session
    ):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        new_label = _label()

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={
                "label": new_label,
                "fields": {"note": NOTE_FIELD},
                "extension_fields": {name: ["serial_number"]},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["label"] == new_label
        assert set(body["fields"]) == {"note", "serial_number"}
        await _assert_stored_copy(db_session, type_id, name, "serial_number")
        assert body["managed_by"] == {"serial_number": name}

    async def _assert_unchanged(self, client, type_id, before):
        after = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert after == before

    async def test_patch_key_already_on_type_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(
            db_session, fields={"serial_number": {"type": "string", "label": "Mine"}}
        )
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={"label": _label(), "extension_fields": {name: ["serial_number"]}},
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == "'serial_number' already exists on this type"
        await self._assert_unchanged(client, type_id, before)

    async def test_patch_clash_with_fields_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={
                "label": _label(),
                "fields": {"serial_number": NOTE_FIELD},
                "extension_fields": {name: ["serial_number"]},
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == "'serial_number' is in both fields and extension_fields"
        await self._assert_unchanged(client, type_id, before)

    async def test_patch_disabled_extension_is_409(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD}, enabled=False)
        type_id = await seed_endpoint_type(db_session)
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={"label": _label(), "extension_fields": {name: ["serial_number"]}},
        )
        assert response.status_code == 409, response.text
        await self._assert_unchanged(client, type_id, before)

    async def test_patch_unknown_extension_or_key_is_404(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        cases = (
            ({"missing_ext": ["serial_number"]}, "Extension 'missing_ext' not found"),
            ({name: ["nope"]}, f"Extension '{name}' declares no endpoint field 'nope'"),
        )
        for extension_fields, message in cases:
            response = await client.patch(
                f"/endpoint-types/{type_id}",
                json={"label": _label(), "extension_fields": extension_fields},
            )
            assert response.status_code == 404, response.text
            assert response.json()["message"] == message
        await self._assert_unchanged(client, type_id, before)

    async def test_patch_unknown_type_with_extension_fields_is_type_404(self, client, db_session):
        enabled = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        disabled = await seed_extension(db_session, {"batch": STRING_FIELD}, enabled=False)
        missing_type = "00000000-0000-0000-0000-000000000000"

        for extension_fields in (
            {enabled: ["serial_number"]},
            {"missing_ext": ["serial_number"]},
            {disabled: ["batch"]},
        ):
            response = await client.patch(
                f"/endpoint-types/{missing_type}", json={"extension_fields": extension_fields}
            )
            assert response.status_code == 404, response.text
            assert response.json()["message"] == f"EndpointType '{missing_type}' not found"

    async def test_patch_role_on_managed_field_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        created = await client.post(
            "/endpoint-types",
            json={"label": _label(), "extension_fields": {name: ["serial_number"]}},
        )
        assert created.status_code == 201, created.text
        type_id = created.json()["type_id"]

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"mapping": {"serial_number": "name"}}
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == ROLE_MESSAGE.format(name)
        assert (await client.get(f"/endpoint-types/{type_id}")).json()["mapping"] == {}

    async def test_patch_role_on_added_extension_field_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={
                "extension_fields": {name: ["serial_number"]},
                "mapping": {"serial_number": "name"},
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == ROLE_MESSAGE.format(name)
        await self._assert_unchanged(client, type_id, before)

    async def test_patch_only_extension_fields_works(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(
            db_session, fields={"title": NOTE_FIELD}, mapping={"title": "name"}
        )

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"extension_fields": {name: ["serial_number"]}}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["fields"]) == {"title", "serial_number"}
        assert body["mapping"] == {"title": "name"}
        assert body["managed_by"] == {"serial_number": name}


PERM_OID = "managed-type-fields-perm-oid"


class TestExtensionFieldsOnTypesPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": PERM_OID,
            "sub": PERM_OID,
            "preferred_username": "managed-type-fields-perm@test.com",
            "name": "Managed Type Fields Perm",
            "roles": [],
        }

    async def _grant(self, db_session, *actions):
        await AbacFixtures(db_session).setup(
            users={"tester": PERM_OID},
            roles={"r": list(actions)},
            teams={"t": {"roles": ["r"], "users": ["tester"]}},
        )

    async def test_platform_config_write_can_create_and_patch(self, client, db_session):
        await self._grant(db_session, "platform.config.write")
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD, "batch": STRING_FIELD})

        created = await client.post(
            "/endpoint-types",
            json={"label": _label(), "extension_fields": {name: ["serial_number"]}},
        )
        assert created.status_code == 201, created.text

        patched = await client.patch(
            f"/endpoint-types/{created.json()['type_id']}",
            json={"extension_fields": {name: ["batch"]}},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["managed_by"] == {"serial_number": name, "batch": name}

    async def test_endpoint_write_cannot_create_or_patch(self, client, db_session):
        await self._grant(db_session, "device.endpoint.write")
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)

        created = await client.post(
            "/endpoint-types",
            json={"label": _label(), "extension_fields": {name: ["serial_number"]}},
        )
        assert created.status_code == 403, created.text

        patched = await client.patch(
            f"/endpoint-types/{type_id}", json={"extension_fields": {name: ["serial_number"]}}
        )
        assert patched.status_code == 403, patched.text
        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["managed_by"] == {}


class TestEmptyExtensionKeyList:
    """D31: an extension listed with no keys is a client bug and is rejected."""

    async def test_create_with_empty_key_list_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        label = _label()
        response = await client.post(
            "/endpoint-types", json={"label": label, "extension_fields": {name: []}}
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == f"extension_fields for '{name}' lists no keys"
        assert label not in await _labels(client)

    async def test_patch_with_empty_key_list_is_422(self, client, db_session):
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        type_id = await seed_endpoint_type(db_session)
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={"label": _label(), "extension_fields": {name: []}},
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == f"extension_fields for '{name}' lists no keys"
        assert (await client.get(f"/endpoint-types/{type_id}")).json() == before

    async def test_empty_extension_fields_object_is_allowed(self, client):
        created = await client.post(
            "/endpoint-types", json={"label": _label(), "extension_fields": {}}
        )
        assert created.status_code == 201, created.text

        patched = await client.patch(
            f"/endpoint-types/{created.json()['type_id']}", json={"extension_fields": {}}
        )
        assert patched.status_code == 200, patched.text


# --- the lock on managed fields and removal (D9, D13, D23) -----------------------------------

LOCK_MESSAGE = (
    "'serial_number' is managed by extension '{}'; "
    "only label, description and show_in_list can change"
)
PATTERN_FIELD = {"type": "string", "label": "Serial number", "validation": {"pattern": "^SN"}}
LOCK_DEVICE = "managed-lock-dev"


async def _managed_type(client, db_session, field=None, extra_fields=None):
    """A type created via POST with the extension's `serial_number` applied. -> (ext, type_id)"""
    name = await seed_extension(db_session, {"serial_number": field or STRING_FIELD})
    created = await client.post(
        "/endpoint-types",
        json={
            "label": _label(),
            "fields": extra_fields or {},
            "extension_fields": {name: ["serial_number"]},
        },
    )
    assert created.status_code == 201, created.text
    return name, created.json()["type_id"]


async def _endpoint_with_serial(client, db_session, type_id) -> str:
    await AbacFixtures(db_session).setup(devices={LOCK_DEVICE: {}})
    created = await client.post(
        f"/endpoints?device_id={LOCK_DEVICE}",
        json={"type_id": type_id, "endpoint_data": {"serial_number": "SN-1"}},
    )
    assert created.status_code == 201, created.text
    return created.json()["endpoint_id"]


class TestManagedFieldLock:
    async def test_patch_label_description_show_in_list_ok(self, client, db_session):
        name, type_id = await _managed_type(client, db_session)

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={
                "fields": {
                    "serial_number": {
                        "label": "Machine serial",
                        "description": "Printed on the plate",
                        "show_in_list": False,
                    }
                }
            },
        )
        assert response.status_code == 200, response.text
        field = response.json()["fields"]["serial_number"]
        assert field["label"] == "Machine serial"
        assert field["description"] == "Printed on the plate"
        assert field["show_in_list"] is False
        assert response.json()["managed_by"] == {"serial_number": name}

    async def test_patch_with_unchanged_full_definition_and_new_label_is_ok(
        self, client, db_session
    ):
        name, type_id = await _managed_type(client, db_session, field=PATTERN_FIELD)
        current = (await client.get(f"/endpoint-types/{type_id}")).json()
        # The app sends back the GET's whole definition, nulls included.
        definition = {**current["fields"]["serial_number"], "label": "Renamed"}
        assert definition["validation"]["min_length"] is None

        response = await client.patch(
            f"/endpoint-types/{type_id}",
            json={"label": current["label"], "fields": {"serial_number": definition}},
        )
        assert response.status_code == 200, response.text
        field = response.json()["fields"]["serial_number"]
        assert field["label"] == "Renamed"
        assert field["validation"]["pattern"] == "^SN"
        assert response.json()["managed_by"] == {"serial_number": name}

    async def test_patch_locked_attribute_is_422(self, client, db_session):
        name, type_id = await _managed_type(client, db_session)
        before = (await client.get(f"/endpoint-types/{type_id}")).json()

        for change in ({"ui": "textarea"}, {"required": True}, {"type": "integer"}):
            response = await client.patch(
                f"/endpoint-types/{type_id}",
                json={"label": _label(), "fields": {"serial_number": change}},
            )
            assert response.status_code == 422, (change, response.text)
            assert response.json()["message"] == LOCK_MESSAGE.format(name)
        assert (await client.get(f"/endpoint-types/{type_id}")).json() == before

    async def test_unmanaged_field_edits_are_not_locked(self, client, db_session):
        _, type_id = await _managed_type(client, db_session, extra_fields={"note": NOTE_FIELD})

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"note": {"ui": "textarea"}}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["note"]["ui"] == "textarea"

    async def test_role_via_mapping_on_managed_field_is_422(self, client, db_session):
        name, type_id = await _managed_type(client, db_session)

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"mapping": {"serial_number": "name"}}
        )
        assert response.status_code == 422, response.text
        assert response.json()["message"] == ROLE_MESSAGE.format(name)

    async def test_removed_field_can_get_a_role_again(self, client, db_session):
        _, type_id = await _managed_type(client, db_session)

        removed = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": None}}
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["managed_by"] == {}

        # Now a normal admin field with the same key, given a role (D13 + D23).
        readded = await client.patch(
            f"/endpoint-types/{type_id}",
            json={
                "fields": {"serial_number": {"type": "string", "label": "Own serial"}},
                "mapping": {"serial_number": "name"},
            },
        )
        assert readded.status_code == 200, readded.text
        assert readded.json()["mapping"] == {"serial_number": "name"}
        assert readded.json()["managed_by"] == {}

        # ...and as an admin field it is no longer locked.
        edited = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": {"ui": "textarea"}}}
        )
        assert edited.status_code == 200, edited.text

    async def test_remove_keeps_other_markers(self, client, db_session):
        name = await seed_extension(
            db_session, {"serial_number": STRING_FIELD, "batch": STRING_FIELD}
        )
        created = await client.post(
            "/endpoint-types",
            json={"label": _label(), "extension_fields": {name: ["serial_number", "batch"]}},
        )
        assert created.status_code == 201, created.text

        response = await client.patch(
            f"/endpoint-types/{created.json()['type_id']}",
            json={"fields": {"serial_number": None}},
        )
        assert response.status_code == 200, response.text
        assert set(response.json()["fields"]) == {"batch"}
        assert response.json()["managed_by"] == {"batch": name}

    async def test_remove_keeps_values_as_custom_fields(self, client, db_session):
        _, type_id = await _managed_type(client, db_session)
        endpoint_id = await _endpoint_with_serial(client, db_session, type_id)

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": None}}
        )
        assert response.status_code == 200, response.text
        assert "serial_number" not in response.json()["fields"]
        assert response.json()["managed_by"] == {}

        endpoint = await client.get(f"/endpoints/{endpoint_id}")
        assert endpoint.status_code == 200, endpoint.text
        assert endpoint.json()["endpoint_data"]["serial_number"] == {"value": "SN-1", "field": None}

    async def test_remove_then_reapply_restores_field_values(self, client, db_session):
        name, type_id = await _managed_type(client, db_session)
        endpoint_id = await _endpoint_with_serial(client, db_session, type_id)
        removed = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": None}}
        )
        assert removed.status_code == 200, removed.text

        applied = await client.post(
            f"/extension-fields/{name}/apply",
            json={"type_ids": [type_id], "keys": ["serial_number"]},
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["applied"] == [{"type_id": type_id, "key": "serial_number"}]
        assert applied.json()["skipped"] == []

        endpoint = (await client.get(f"/endpoints/{endpoint_id}")).json()
        assert endpoint["endpoint_data"]["serial_number"]["value"] == "SN-1"
        assert endpoint["endpoint_data"]["serial_number"]["field"] is not None
        et = (await client.get(f"/endpoint-types/{type_id}")).json()
        assert et["managed_by"] == {"serial_number": name}


LOCK_PERM_OID = "managed-field-edit-perm-oid"


class TestManagedFieldEditPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": LOCK_PERM_OID,
            "sub": LOCK_PERM_OID,
            "preferred_username": "managed-field-edit-perm@test.com",
            "name": "Managed Field Edit Perm",
            "roles": [],
        }

    async def _managed_type_id(self, db_session) -> str:
        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        created = await _endpoint_repo(db_session).create_endpoint_type(
            label=_label(),
            description=None,
            fields={"serial_number": SERIAL_FIELD},
            mapping={},
            managed_by={"serial_number": name},
        )
        return created["type_id"]

    async def _grant(self, db_session, *actions):
        await AbacFixtures(db_session).setup(
            users={"tester": LOCK_PERM_OID},
            roles={"r": list(actions)},
            teams={"t": {"roles": ["r"], "users": ["tester"]}},
        )

    async def test_platform_config_write_can_relabel(self, client, db_session):
        await self._grant(db_session, "platform.config.write")
        type_id = await self._managed_type_id(db_session)

        response = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": {"label": "Mine"}}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["serial_number"]["label"] == "Mine"

    async def test_endpoint_write_cannot_relabel_or_remove(self, client, db_session):
        await self._grant(db_session, "device.endpoint.write")
        type_id = await self._managed_type_id(db_session)

        for patch in ({"serial_number": {"label": "Mine"}}, {"serial_number": None}):
            response = await client.patch(f"/endpoint-types/{type_id}", json={"fields": patch})
            assert response.status_code == 403, response.text
        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["fields"]["serial_number"]["label"] == "Serial"
        assert et["managed_by"] != {}
