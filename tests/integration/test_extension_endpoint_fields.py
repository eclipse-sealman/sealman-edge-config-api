"""An extension manifest's `endpoint_fields` section: validation, storage, GET output, and how
a manifest update changes the endpoint types the fields are applied to."""
import pytest

from tests.integration.extension_fixtures import STRING_FIELD, manifest, unique_extension_name


async def _register(client, name, endpoint_fields=None):
    return await client.post("/extensions", json=manifest(name, endpoint_fields))


class TestManifestEndpointFields:
    async def test_register_stores_and_returns_fields(self, client):
        name = unique_extension_name()
        created = await _register(client, name, {"serial_number": STRING_FIELD})
        assert created.status_code == 201, created.text
        assert created.json()["endpoint_fields"]["serial_number"]["label"] == "Serial number"

        detail = await client.get(f"/extensions/{name}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["endpoint_fields"]["serial_number"]["label"] == "Serial number"
        assert detail.json()["endpoint_fields"]["serial_number"]["ui"] == "input"

        listing = await client.get("/extensions")
        assert listing.status_code == 200, listing.text
        item = next(ext for ext in listing.json() if ext["name"] == name)
        assert item["endpoint_fields"]["serial_number"]["label"] == "Serial number"

    async def test_register_without_section_returns_empty_object(self, client):
        name = unique_extension_name()
        created = await _register(client, name)
        assert created.status_code == 201, created.text
        assert created.json()["endpoint_fields"] == {}

        detail = await client.get(f"/extensions/{name}")
        assert detail.json()["endpoint_fields"] == {}

    async def test_register_required_true_is_422_and_not_persisted(self, client):
        name = unique_extension_name()
        response = await _register(client, name, {"serial_number": {**STRING_FIELD, "required": True}})
        assert response.status_code == 422, response.text
        assert "Endpoint field 'serial_number' cannot be required" in response.text

        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_register_invalid_definition_is_422(self, client):
        name = unique_extension_name()
        response = await _register(client, name, {"serial_number": {"type": "string"}})
        assert response.status_code == 422, response.text
        assert "Invalid endpoint field 'serial_number'" in response.text

        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_register_non_object_definition_is_422(self, client):
        name = unique_extension_name()
        response = await _register(client, name, {"serial_number": "string"})
        assert response.status_code == 422, response.text
        assert "Invalid endpoint field 'serial_number'" in response.text

        assert (await client.get(f"/extensions/{name}")).status_code == 404

    async def test_put_without_section_keeps_stored_fields(self, client):
        name = unique_extension_name()
        assert (await _register(client, name, {"serial_number": STRING_FIELD})).status_code == 201

        replaced = await client.put(f"/extensions/{name}", json=manifest(name))
        assert replaced.status_code == 200, replaced.text
        assert list(replaced.json()["endpoint_fields"]) == ["serial_number"]

        detail = await client.get(f"/extensions/{name}")
        assert list(detail.json()["endpoint_fields"]) == ["serial_number"]

    async def test_put_null_section_keeps_stored_fields(self, client):
        name = unique_extension_name()
        assert (await _register(client, name, {"serial_number": STRING_FIELD})).status_code == 201

        body = {**manifest(name), "endpoint_fields": None}
        replaced = await client.put(f"/extensions/{name}", json=body)
        assert replaced.status_code == 200, replaced.text
        assert list(replaced.json()["endpoint_fields"]) == ["serial_number"]

    async def test_put_empty_object_clears_fields(self, client):
        name = unique_extension_name()
        assert (await _register(client, name, {"serial_number": STRING_FIELD})).status_code == 201

        replaced = await client.put(f"/extensions/{name}", json=manifest(name, {}))
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["endpoint_fields"] == {}

        detail = await client.get(f"/extensions/{name}")
        assert detail.json()["endpoint_fields"] == {}

    async def test_put_replaces_fields(self, client):
        name = unique_extension_name()
        assert (await _register(client, name, {"serial_number": STRING_FIELD})).status_code == 201

        replaced = await client.put(f"/extensions/{name}", json=manifest(name, {"asset_tag": STRING_FIELD}))
        assert replaced.status_code == 200, replaced.text
        assert list(replaced.json()["endpoint_fields"]) == ["asset_tag"]

        detail = await client.get(f"/extensions/{name}")
        assert list(detail.json()["endpoint_fields"]) == ["asset_tag"]

    async def test_put_invalid_definition_is_422_and_keeps_stored_fields(self, client):
        name = unique_extension_name()
        assert (await _register(client, name, {"serial_number": STRING_FIELD})).status_code == 201

        body = manifest(name, {"asset_tag": {**STRING_FIELD, "required": True}})
        replaced = await client.put(f"/extensions/{name}", json=body)
        assert replaced.status_code == 422, replaced.text

        detail = await client.get(f"/extensions/{name}")
        assert list(detail.json()["endpoint_fields"]) == ["serial_number"]

    async def test_schema_lists_endpoint_fields(self, client):
        response = await client.get("/extensions/schema")
        assert response.status_code == 200, response.text
        assert "endpoint_fields" in response.text


class TestEndpointFieldOwnership:
    async def test_register_key_owned_by_other_is_409(self, client):
        owner = unique_extension_name()
        other = unique_extension_name()
        assert (await _register(client, owner, {"serial_number": STRING_FIELD})).status_code == 201

        response = await _register(client, other, {"serial_number": STRING_FIELD})
        assert response.status_code == 409, response.text
        assert f"Endpoint field 'serial_number' is already declared by extension '{owner}'" in response.text

        assert (await client.get(f"/extensions/{other}")).status_code == 404

    async def test_put_key_owned_by_other_is_409(self, client):
        owner = unique_extension_name()
        other = unique_extension_name()
        assert (await _register(client, owner, {"serial_number": STRING_FIELD})).status_code == 201
        assert (await _register(client, other, {"asset_tag": STRING_FIELD})).status_code == 201

        body = manifest(other, {"asset_tag": STRING_FIELD, "serial_number": STRING_FIELD})
        response = await client.put(f"/extensions/{other}", json=body)
        assert response.status_code == 409, response.text
        assert f"Endpoint field 'serial_number' is already declared by extension '{owner}'" in response.text

        detail = await client.get(f"/extensions/{other}")
        assert list(detail.json()["endpoint_fields"]) == ["asset_tag"]

    async def test_put_own_keys_is_not_a_conflict(self, client):
        name = unique_extension_name()
        assert (await _register(client, name, {"serial_number": STRING_FIELD})).status_code == 201

        response = await client.put(f"/extensions/{name}", json=manifest(name, {"serial_number": STRING_FIELD}))
        assert response.status_code == 200, response.text

    async def test_key_free_again_after_owner_drops_it(self, client):
        owner = unique_extension_name()
        other = unique_extension_name()
        assert (await _register(client, owner, {"serial_number": STRING_FIELD})).status_code == 201
        assert (await client.put(f"/extensions/{owner}", json=manifest(owner, {}))).status_code == 200

        response = await _register(client, other, {"serial_number": STRING_FIELD})
        assert response.status_code == 201, response.text


TEXTAREA_FIELD = {**STRING_FIELD, "ui": "textarea"}
UPDATE_DEVICE = "manifest-update-dev"
UPDATE_USER_OID = "manifest-update-perm-oid"


async def _registered_with_applied_type(client, endpoint_fields=None):
    """Registers and enables an extension, then applies its fields to a new type. -> (name, type_id)"""
    endpoint_fields = endpoint_fields or {"serial_number": STRING_FIELD}
    name = unique_extension_name()
    assert (await _register(client, name, endpoint_fields)).status_code == 201
    assert (await client.post(f"/extensions/{name}/enable")).status_code == 200
    created = await client.post("/endpoint-types", json={"label": f"type-{name}", "fields": {}})
    assert created.status_code == 201, created.text
    type_id = created.json()["type_id"]
    applied = await client.post(
        f"/extension-fields/{name}/apply", json={"type_ids": [type_id], "keys": list(endpoint_fields)}
    )
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["applied"]) == len(endpoint_fields)
    return name, type_id


async def _get_type(client, type_id):
    response = await client.get(f"/endpoint-types/{type_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _put(client, name, endpoint_fields):
    return await client.put(f"/extensions/{name}", json=manifest(name, endpoint_fields))


class TestManifestUpdateOnTypes:
    async def test_removed_key_becomes_admin_field(self, client):
        name, type_id = await _registered_with_applied_type(
            client, {"serial_number": STRING_FIELD, "batch": STRING_FIELD}
        )
        before = await _get_type(client, type_id)

        response = await _put(client, name, {"batch": STRING_FIELD})
        assert response.status_code == 200, response.text

        after = await _get_type(client, type_id)
        assert after["managed_by"] == {"batch": name}
        assert after["fields"]["serial_number"] == before["fields"]["serial_number"]

        relock = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": TEXTAREA_FIELD}}
        )
        assert relock.status_code == 200, relock.text
        role = await client.patch(f"/endpoint-types/{type_id}", json={"mapping": {"serial_number": "name"}})
        assert role.status_code == 200, role.text

    async def test_removed_last_key_clears_marker(self, client):
        name, type_id = await _registered_with_applied_type(client)

        assert (await _put(client, name, {})).status_code == 200

        after = await _get_type(client, type_id)
        assert after["managed_by"] == {}
        assert after["fields"]["serial_number"]["label"] == "Serial number"

    async def test_changed_definition_updates_type(self, client):
        name, type_id = await _registered_with_applied_type(client)

        response = await _put(client, name, {"serial_number": TEXTAREA_FIELD})
        assert response.status_code == 200, response.text

        after = await _get_type(client, type_id)
        assert after["fields"]["serial_number"]["ui"] == "textarea"
        assert after["managed_by"] == {"serial_number": name}

    async def test_changed_definition_keeps_admin_label(self, client):
        name, type_id = await _registered_with_applied_type(client)
        relabel = await client.patch(
            f"/endpoint-types/{type_id}", json={"fields": {"serial_number": {**STRING_FIELD, "label": "Mine"}}}
        )
        assert relabel.status_code == 200, relabel.text

        response = await _put(client, name, {"serial_number": {**TEXTAREA_FIELD, "label": "Serial no."}})
        assert response.status_code == 200, response.text

        field = (await _get_type(client, type_id))["fields"]["serial_number"]
        assert field["label"] == "Mine"
        assert field["ui"] == "textarea"

    async def test_changed_definition_follows_unchanged_label(self, client):
        name, type_id = await _registered_with_applied_type(client)

        response = await _put(client, name, {"serial_number": {**STRING_FIELD, "label": "Serial no."}})
        assert response.status_code == 200, response.text

        assert (await _get_type(client, type_id))["fields"]["serial_number"]["label"] == "Serial no."

    async def test_changed_definition_only_touches_types_managed_by_the_extension(self, client, db_session):
        name, type_id = await _registered_with_applied_type(client)
        other_type = await client.post(
            "/endpoint-types", json={"label": f"admin-{name}", "fields": {"serial_number": STRING_FIELD}}
        )
        assert other_type.status_code == 201, other_type.text
        other_id = other_type.json()["type_id"]
        other_before = await _get_type(client, other_id)

        assert (await _put(client, name, {"serial_number": TEXTAREA_FIELD})).status_code == 200

        assert await _get_type(client, other_id) == other_before
        assert (await _get_type(client, type_id))["fields"]["serial_number"]["ui"] == "textarea"

    async def test_changed_definition_never_touches_endpoint_values(self, client, db_session):
        from sqlalchemy import select

        from db.models.endpoint import Endpoint
        from tests.integration.endpoint_fixtures import seed_endpoint
        from tests.integration.test_abac_authorization import AbacFixtures

        name, type_id = await _registered_with_applied_type(client)
        await AbacFixtures(db_session).setup(devices={UPDATE_DEVICE: {}})
        endpoint_id = await seed_endpoint(db_session, UPDATE_DEVICE, type_id, {"serial_number": "SN-1", "x": 1})

        async def _stored():
            return await db_session.scalar(
                select(Endpoint.endpoint_data).where(Endpoint.endpoint_id == endpoint_id)
            )

        before = await _stored()
        changed = {"serial_number": {"type": "number", "label": "Serial number"}}
        assert (await _put(client, name, changed)).status_code == 200

        assert await _stored() == before == {"serial_number": "SN-1", "x": 1}

    async def test_new_key_is_not_applied(self, client):
        name, type_id = await _registered_with_applied_type(client)
        before = await _get_type(client, type_id)

        response = await _put(client, name, {"serial_number": STRING_FIELD, "batch": STRING_FIELD})
        assert response.status_code == 200, response.text

        after = await _get_type(client, type_id)
        assert after["managed_by"] == before["managed_by"] == {"serial_number": name}
        assert "batch" not in after["fields"]
        listing = (await client.get("/extension-fields")).json()
        item = next(entry for entry in listing if entry["extension"] == name)
        assert item["applied"]["batch"] == []

    async def test_put_without_section_leaves_types_unchanged(self, client):
        name, type_id = await _registered_with_applied_type(client)
        before = await _get_type(client, type_id)

        response = await client.put(f"/extensions/{name}", json=manifest(name))
        assert response.status_code == 200, response.text

        assert await _get_type(client, type_id) == before

    async def test_put_null_section_leaves_types_unchanged(self, client):
        name, type_id = await _registered_with_applied_type(client)
        before = await _get_type(client, type_id)

        body = {**manifest(name), "endpoint_fields": None}
        response = await client.put(f"/extensions/{name}", json=body)
        assert response.status_code == 200, response.text

        assert await _get_type(client, type_id) == before

    async def test_update_while_disabled_still_updates_types(self, client):
        name, type_id = await _registered_with_applied_type(
            client, {"serial_number": STRING_FIELD, "batch": STRING_FIELD}
        )
        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200

        response = await _put(client, name, {"serial_number": TEXTAREA_FIELD})
        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is False

        after = await _get_type(client, type_id)
        assert after["fields"]["serial_number"]["ui"] == "textarea"
        assert after["managed_by"] == {"serial_number": name}

    async def test_failing_update_leaves_types_unchanged(self, client, monkeypatch):
        from db.sqlalchemy.extension import SqlAlchemyExtensionRepository
        from exceptions import APIError

        name, type_id = await _registered_with_applied_type(
            client, {"serial_number": STRING_FIELD, "batch": STRING_FIELD}
        )
        before = await _get_type(client, type_id)

        async def _fail(self, *args, **kwargs):
            raise APIError("simulated", 500)

        monkeypatch.setattr(SqlAlchemyExtensionRepository, "replace_upstreams_and_routes", _fail)
        response = await _put(client, name, {"serial_number": TEXTAREA_FIELD})
        assert response.status_code == 500, response.text
        monkeypatch.undo()

        assert await _get_type(client, type_id) == before
        detail = (await client.get(f"/extensions/{name}")).json()
        assert set(detail["endpoint_fields"]) == {"serial_number", "batch"}


class TestManifestUpdatePermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": UPDATE_USER_OID,
            "sub": UPDATE_USER_OID,
            "preferred_username": "manifest-update-perm@test.com",
            "name": "Manifest Update Perm",
            "roles": [],
        }

    async def _grant(self, db_session, *actions):
        from tests.integration.test_abac_authorization import AbacFixtures

        await AbacFixtures(db_session).setup(
            users={"tester": UPDATE_USER_OID},
            roles={"r": list(actions)},
            teams={"t": {"roles": ["r"], "users": ["tester"]}},
        )

    async def _seed(self, db_session):
        from tests.integration.endpoint_fixtures import _endpoint_repo, unique_label
        from tests.integration.extension_fixtures import seed_extension

        name = await seed_extension(db_session, {"serial_number": STRING_FIELD, "batch": STRING_FIELD})
        created = await _endpoint_repo(db_session).create_endpoint_type(
            label=unique_label("et"),
            description=None,
            fields={"serial_number": STRING_FIELD, "batch": STRING_FIELD},
            mapping={},
            managed_by={"serial_number": name, "batch": name},
        )
        return name, created["type_id"]

    async def test_extension_register_can_put_and_types_are_updated(self, client, db_session):
        from tests.integration.endpoint_fixtures import _endpoint_repo

        await self._grant(db_session, "extension.register")
        name, type_id = await self._seed(db_session)

        response = await _put(client, name, {"serial_number": TEXTAREA_FIELD})
        assert response.status_code == 200, response.text

        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["fields"]["serial_number"]["ui"] == "textarea"
        assert et["managed_by"] == {"serial_number": name}

    async def test_platform_config_write_alone_cannot_put(self, client, db_session):
        from tests.integration.endpoint_fixtures import _endpoint_repo

        await self._grant(db_session, "platform.config.write")
        name, type_id = await self._seed(db_session)

        response = await _put(client, name, {"serial_number": TEXTAREA_FIELD})
        assert response.status_code == 403, response.text

        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["managed_by"] == {"serial_number": name, "batch": name}
        assert et["fields"]["serial_number"] == STRING_FIELD


UNINSTALL_DEVICE = "uninstall-dev"
UNINSTALL_USER_OID = "uninstall-perm-oid"


async def _apply_to_new_type(client, name, keys):
    created = await client.post("/endpoint-types", json={"label": f"type2-{name}", "fields": {}})
    assert created.status_code == 201, created.text
    type_id = created.json()["type_id"]
    applied = await client.post(f"/extension-fields/{name}/apply", json={"type_ids": [type_id], "keys": keys})
    assert applied.status_code == 200, applied.text
    assert len(applied.json()["applied"]) == len(keys)
    return type_id


class TestUninstall:
    async def test_delete_removes_markers_on_all_types_and_keeps_fields_and_values(self, client, db_session):
        from sqlalchemy import select

        from db.models.endpoint import Endpoint
        from tests.integration.endpoint_fixtures import seed_endpoint
        from tests.integration.test_abac_authorization import AbacFixtures

        name, first_id = await _registered_with_applied_type(client)
        second_id = await _apply_to_new_type(client, name, ["serial_number"])
        before = {type_id: await _get_type(client, type_id) for type_id in (first_id, second_id)}
        await AbacFixtures(db_session).setup(devices={UNINSTALL_DEVICE: {}})
        endpoint_id = await seed_endpoint(db_session, UNINSTALL_DEVICE, first_id, {"serial_number": "SN-1"})

        response = await client.delete(f"/extensions/{name}")
        assert response.status_code == 204, response.text

        for type_id in (first_id, second_id):
            after = await _get_type(client, type_id)
            assert after["managed_by"] == {}
            assert after["fields"] == before[type_id]["fields"]

        stored = await db_session.scalar(select(Endpoint.endpoint_data).where(Endpoint.endpoint_id == endpoint_id))
        assert stored == {"serial_number": "SN-1"}
        endpoint = (await client.get(f"/endpoints/{endpoint_id}")).json()
        assert endpoint["endpoint_data"]["serial_number"]["value"] == "SN-1"
        assert endpoint["endpoint_data"]["serial_number"]["field"] is not None

        role = await client.patch(f"/endpoint-types/{first_id}", json={"mapping": {"serial_number": "name"}})
        assert role.status_code == 200, role.text

    async def test_disable_keeps_markers(self, client):
        name, type_id = await _registered_with_applied_type(client)
        before = await _get_type(client, type_id)

        assert (await client.post(f"/extensions/{name}/disable")).status_code == 200

        after = await _get_type(client, type_id)
        assert after["managed_by"] == {"serial_number": name}
        assert after == before

    async def test_failing_delete_keeps_markers(self, client, monkeypatch):
        from db.sqlalchemy.extension import SqlAlchemyExtensionRepository
        from exceptions import APIError

        name, type_id = await _registered_with_applied_type(client)
        before = await _get_type(client, type_id)

        async def _fail(self, *args, **kwargs):
            raise APIError("simulated failure after markers were released", 500)

        monkeypatch.setattr(SqlAlchemyExtensionRepository, "delete_orphaned_actions", _fail)
        response = await client.delete(f"/extensions/{name}")
        assert response.status_code == 500, response.text
        monkeypatch.undo()

        assert await _get_type(client, type_id) == before
        assert (await client.get(f"/extensions/{name}")).status_code == 200


class TestUninstallPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": UNINSTALL_USER_OID,
            "sub": UNINSTALL_USER_OID,
            "preferred_username": "uninstall-perm@test.com",
            "name": "Uninstall Perm",
            "roles": [],
        }

    async def _grant(self, db_session, *actions):
        from tests.integration.test_abac_authorization import AbacFixtures

        await AbacFixtures(db_session).setup(
            users={"tester": UNINSTALL_USER_OID},
            roles={"r": list(actions)},
            teams={"t": {"roles": ["r"], "users": ["tester"]}},
        )

    async def _seed(self, db_session):
        from tests.integration.endpoint_fixtures import _endpoint_repo, unique_label
        from tests.integration.extension_fixtures import seed_extension

        name = await seed_extension(db_session, {"serial_number": STRING_FIELD})
        created = await _endpoint_repo(db_session).create_endpoint_type(
            label=unique_label("et"),
            description=None,
            fields={"serial_number": STRING_FIELD},
            mapping={},
            managed_by={"serial_number": name},
        )
        return name, created["type_id"]

    async def test_extension_deregister_alone_can_delete_and_markers_are_released(self, client, db_session):
        from tests.integration.endpoint_fixtures import _endpoint_repo

        await self._grant(db_session, "extension.deregister")
        name, type_id = await self._seed(db_session)

        response = await client.delete(f"/extensions/{name}")
        assert response.status_code == 204, response.text

        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["managed_by"] == {}
        assert et["fields"]["serial_number"] == STRING_FIELD

    async def test_platform_config_write_alone_cannot_delete(self, client, db_session):
        from tests.integration.endpoint_fixtures import _endpoint_repo

        await self._grant(db_session, "platform.config.write")
        name, type_id = await self._seed(db_session)

        response = await client.delete(f"/extensions/{name}")
        assert response.status_code == 403, response.text

        et = await _endpoint_repo(db_session).get_endpoint_type(type_id)
        assert et["managed_by"] == {"serial_number": name}
