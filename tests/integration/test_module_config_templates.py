"""Module configuration templates: library, versioning, device apply and the revision history."""
import uuid

import pytest
from cryptography.fernet import Fernet

from routers.module_config import service
from tests.integration.endpoint_fixtures import (
    _endpoint_repo, _service_repo, seed_endpoint, seed_endpoint_type, seed_service, seed_service_type, unique_label)
from tests.integration.test_abac_authorization import AbacFixtures
from tests.pem_fixtures import make_cert, make_key

DEVICE = "mct-dev-1"
MODULE = "seal-demo-module"
NET_DISCOVER = "seal-app-net-discover"
NET_DISCOVER_CONFIG = {
    "scheduledCron": "0 * * * *",
    "scanDefinition": {"networkDefinition": "10.0.0.0", "subnetMask": 24, "ports": [502]},
    "endpointNames": {},
}

TEMPLATE = {
    "name": "net-discover-basic",
    "description": "Basic discovery",
    "body": {"interval": "${interval}", "site": "${device.site}", "ranges": ["${range}"], "debug": "${debug}"},
    "variables": [
        {"name": "range", "type": "string"},
        {"name": "interval", "type": "integer"},
        {"name": "debug", "type": "boolean", "required": False},
    ],
    "comment": "first",
}


@pytest.fixture
def hub(monkeypatch):
    """In-memory IoT Hub module twins + blob storage behind the service."""
    state = {"blobs": {}, "desired": {}}

    async def upload_and_patch(device, module, content):
        config_id = uuid.uuid4().hex
        state["blobs"][(device, module, config_id)] = content
        state["desired"][(device, module)] = {"configId": config_id, "configBlobUrl": f"blob://{config_id}", "_blob": config_id}
        return config_id

    async def desired(device, module):
        return state["desired"].get((device, module), {})

    async def read_blob(device, module, name, missing_ok=False):
        return state["blobs"].get((device, module, name))

    monkeypatch.setattr(service, "_upload_and_patch", upload_and_patch)
    monkeypatch.setattr(service, "get_module_twin_desired", desired)
    monkeypatch.setattr(service, "read_module_config_blob", read_blob)
    monkeypatch.setattr(service, "blob_name_from_desired", lambda device, module, d: d["_blob"])
    return state


@pytest.fixture
async def device(db_session):
    await AbacFixtures(db_session).setup(devices={DEVICE: {"site": "Aachen"}})
    return DEVICE


async def _group(client, name="demo", modules=(MODULE,), scope_ids=()):
    response = await client.post("/module-config-template-groups", json={
        "name": name, "module_names": list(modules), "scope_ids": [str(i) for i in scope_ids]})
    assert response.status_code == 201, response.text
    return response.json()


async def _create(client, module_names=(MODULE,), group=None, **overrides):
    """Creates a template in the group for `module_names` (created on demand, one per module list)."""
    if group is None:
        name = "ns " + "".join(c if c.isalnum() else "-" for c in ",".join(module_names))
        existing = [n for n in (await client.get("/module-config-template-groups")).json() if n["name"] == name]
        group = existing[0] if existing else await _group(client, name, module_names)
    response = await client.post("/module-config-templates", json={**TEMPLATE, "group_id": group["id"], **overrides})
    assert response.status_code == 201, response.text
    return response.json()


class TestTemplateLibrary:
    async def test_create_edit_versions_and_filter_by_module(self, client, db_session):
        created = await _create(client)
        assert created["latest_version"] == 1 and created["created_by"]

        edited = {**TEMPLATE, "body": {**TEMPLATE["body"], "extra": 1}, "comment": "second"}
        response = await client.post(f"/module-config-templates/{created['id']}/versions", json=edited)
        assert response.status_code == 201, response.text
        assert response.json()["version"] == 2
        assert response.json()["references"] == {"variables": ["debug", "interval", "range"], "device": ["site"], "endpoints": [], "endpoint_fields": []}

        versions = (await client.get(f"/module-config-templates/{created['id']}/versions")).json()
        assert [v["version"] for v in versions] == [2, 1]
        v1 = (await client.get(f"/module-config-templates/{created['id']}/versions/1")).json()
        assert "extra" not in v1["body"]
        latest = (await client.get(f"/module-config-templates/{created['id']}/versions/latest")).json()
        assert latest["body"]["extra"] == 1
        assert (await client.get(f"/module-config-templates/{created['id']}")).json()["latest_version"] == 2

        names = lambda r: [t["name"] for t in r.json()]
        assert "net-discover-basic" in names(await client.get(f"/module-config-templates?module={MODULE}"))
        assert "net-discover-basic" not in names(await client.get("/module-config-templates?module=seal-opcua"))

    async def test_invalid_template_and_duplicate_name_are_rejected(self, client, db_session):
        group = await _group(client)
        response = await client.post("/module-config-templates", json={**TEMPLATE, "group_id": group["id"], "body": {"a": "${nope}"}})
        assert response.status_code == 422
        assert response.json()["findings"][0]["code"] == "unknown-variable"
        await _create(client)
        assert (await client.post("/module-config-templates", json={**TEMPLATE, "group_id": group["id"]})).status_code == 409

    async def test_duplicate_archive_and_restore(self, client, db_session):
        source = await _create(client)
        response = await client.post(f"/module-config-templates/{source['id']}/duplicate", json={"name": "copy"})
        assert response.status_code == 201, response.text
        copy = response.json()
        assert copy["derived_from_template_id"] == source["id"] and copy["derived_from_version"] == 1
        assert copy["latest_version"] == 1

        assert (await client.delete(f"/module-config-templates/{copy['id']}")).json()["archived"] is True
        listed = [t["name"] for t in (await client.get("/module-config-templates")).json()]
        assert "copy" not in listed
        response = await client.post(f"/module-config-templates/{copy['id']}/versions", json=TEMPLATE)
        assert response.status_code == 409
        restored = await client.patch(f"/module-config-templates/{copy['id']}", json={"archived": False})
        assert restored.json()["archived"] is False

    async def test_lint_and_preview(self, client, db_session):
        lint = (await client.post("/module-config-templates/lint", json={"body": {"a": "${x}"}, "variables": []})).json()
        assert lint["ok"] is False
        preview = (await client.post("/module-config-templates/preview", json={
            **TEMPLATE, "values": {"range": "10.0.0.0/24", "interval": 60}, "device": {"site": "Demo"}})).json()
        assert preview["ok"] is True
        assert preview["config"] == {"interval": 60, "site": "Demo", "ranges": ["10.0.0.0/24"]}


class TestDeviceModuleConfig:
    async def test_render_apply_history_and_reapply(self, client, db_session, hub, device):
        template = await _create(client)

        render = (await client.post(f"/{device}/modules/{MODULE}/config/render",
                                    json={"template_id": template["id"], "values": {}})).json()
        assert render["ok"] is False
        assert render["findings"][0]["path"] == "/values/range"

        response = await client.post(f"/{device}/modules/{MODULE}/config/apply", json={
            "template_id": template["id"], "values": {"range": "10.0.0.0/24", "interval": 60, "debug": "true"}, "comment": "go"})
        assert response.status_code == 200, response.text
        applied = response.json()
        content = {"interval": 60, "site": "Aachen", "ranges": ["10.0.0.0/24"], "debug": True}
        assert hub["blobs"][(device, MODULE, applied["configId"])] == content
        assert applied["revision"]["source"] == "template"

        raw = await client.post(f"/{device}/twin/config/{MODULE}", json={"interval": 5})
        assert raw.status_code == 200, raw.text

        history = (await client.get(f"/{device}/modules/{MODULE}/config/revisions")).json()
        assert [r["source"] for r in history["revisions"]] == ["raw", "template"]
        assert history["revisions"][0]["current"] is True
        assert history["revisions"][0]["parent_revision_id"] == history["revisions"][1]["id"]
        assert history["revisions"][1]["template_name"] == "net-discover-basic"

        template_revision = history["revisions"][1]["id"]
        detail = (await client.get(f"/{device}/modules/{MODULE}/config/revisions/{template_revision}")).json()
        assert detail["content"] == content and detail["values"]["range"] == "10.0.0.0/24"

        response = await client.post(f"/{device}/modules/{MODULE}/config/revisions/{template_revision}/reapply", json={})
        assert response.status_code == 200, response.text
        reapplied = response.json()["revision"]
        assert reapplied["source"] == "reapply" and reapplied["reapplied_from_id"] == template_revision
        assert hub["blobs"][(device, MODULE, reapplied["config_id"])] == content

    async def test_render_uses_latest_version(self, client, db_session, hub, device):
        template = await _create(client, body={"a": 1}, variables=[])
        second = await client.post(f"/module-config-templates/{template['id']}/versions", json={"body": {"a": 2}})
        assert second.status_code == 201, second.text
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render",
                                    json={"template_id": template["id"], "version": 1})).json()
        assert render["ok"] is True and render["config"] == {"a": 2} and render["version"] == 2

    async def test_template_must_match_module(self, client, db_session, hub, device):
        template = await _create(client)
        response = await client.post(f"/{device}/modules/seal-opcua-client/config/apply",
                                     json={"template_id": template["id"], "values": {"range": "x"}})
        assert response.status_code == 422

    async def test_external_write_is_recorded_once(self, client, db_session, hub, device):
        hub["desired"][(device, MODULE)] = {"configId": "legacy1", "configBlobUrl": "blob://current", "_blob": "current"}
        hub["blobs"][(device, MODULE, "current")] = {"old": True}
        for _ in range(2):
            history = (await client.get(f"/{device}/modules/{MODULE}/config/revisions")).json()
        assert [(r["source"], r["blob_name"], r["current"]) for r in history["revisions"]] == [("external", "current", True)]
        response = await client.post(
            f"/{device}/modules/{MODULE}/config/revisions/{history['revisions'][0]['id']}/reapply", json={})
        assert response.status_code == 409

    async def test_typed_legacy_endpoint_is_recorded(self, client, db_session, hub, device):
        response = await client.post(f"/{device}/twin/config/{NET_DISCOVER}", json=NET_DISCOVER_CONFIG)
        assert response.status_code == 200, response.text
        history = (await client.get(f"/{device}/modules/{NET_DISCOVER}/config/revisions")).json()["revisions"]
        assert [r["source"] for r in history] == ["legacy-api"]
        assert history[0]["actor"]

    async def test_deployed_opcua_module_name_is_schema_checked_too(self, client, db_session, hub, device):
        bad = await _create(client, name="opcua-bad", module_names=["seal-opcua-client"], body={"clients": "${x}"},
                            variables=[{"name": "x"}])
        render = (await client.post(f"/{device}/modules/seal-opcua-client/config/render",
                                    json={"template_id": bad["id"], "values": {"x": "not-an-object"}})).json()
        assert render["ok"] is False
        assert any(f["code"] == "schema" for f in render["findings"])

    async def test_known_module_schema_is_enforced_on_apply(self, client, db_session, hub, device):
        bad = await _create(client, name="nd-bad", module_names=[NET_DISCOVER], body={"scheduledCron": "${cron}"},
                            variables=[{"name": "cron"}])
        render = (await client.post(f"/{device}/modules/{NET_DISCOVER}/config/render",
                                    json={"template_id": bad["id"], "values": {"cron": "0 * * * *"}})).json()
        assert render["ok"] is False
        assert {f["path"] for f in render["findings"] if f["code"] == "schema"} >= {"/scanDefinition", "/endpointNames"}
        response = await client.post(f"/{device}/modules/{NET_DISCOVER}/config/apply",
                                     json={"template_id": bad["id"], "values": {"cron": "0 * * * *"}})
        assert response.status_code == 422 and not hub["blobs"]

        good = await _create(client, name="nd-good", module_names=[NET_DISCOVER],
                             body={**NET_DISCOVER_CONFIG, "scheduledCron": "${cron}"}, variables=[{"name": "cron"}])
        response = await client.post(f"/{device}/modules/{NET_DISCOVER}/config/apply",
                                     json={"template_id": good["id"], "values": {"cron": "30 2 * * *"}})
        assert response.status_code == 200, response.text

    async def test_extension_twin_write_records_through_own_session(self, monkeypatch, hub):
        recorded = {}

        async def fake_record(repo, fields):
            recorded.update(fields, repo=repo)
            return None

        monkeypatch.setattr(service, "_record", fake_record)
        from extensions.upstreams import iotedge
        result = await iotedge._twin_write(DEVICE, MODULE, {"a": 1}, source="extension:demo", actor="bob")
        assert result["configId"] == recorded["config_id"]
        assert recorded["source"] == "extension:demo" and recorded["actor"] == "bob" and recorded["repo"] is None


class TestExampleDevices:
    async def test_group_scopes_limit_the_example_devices_and_the_preview_uses_their_metadata(self, client, db_session):
        world = await AbacFixtures(db_session).setup(
            scopes={"aachen": {"attr": {"city": "Aachen"}, "access_rule": "ALL"}},
            devices={"ex-aachen": {"city": "Aachen", "description": "Test bench"}, "ex-duesseldorf": {"city": "Duesseldorf"}},
        )
        scoped = await _group(client, "Aachen only", [MODULE], [world.scopes["aachen"]])
        everyone = await _group(client, "Everyone", [MODULE])
        ids = lambda r: [d["device_id"] for d in r.json()]
        url = lambda ns: f"/module-config-template-groups/{ns['id']}/example-devices"

        assert ids(await client.get(url(scoped))) == ["ex-aachen"]
        assert ids(await client.get(url(everyone))) == ["ex-aachen", "ex-duesseldorf"]
        assert ids(await client.get(url(everyone) + "?q=bench")) == ["ex-aachen"]
        assert ids(await client.get(url(everyone) + "?q=duessel")) == ["ex-duesseldorf"]
        assert (await client.get(url(scoped))).json()[0]["device_data"]["description"] == "Test bench"
        assert (await client.get("/module-config-template-groups/999999/example-devices")).status_code == 404

        body = {"body": {"city": "${device.city}"}, "variables": [], "group_id": scoped["id"]}
        preview = await client.post("/module-config-templates/preview", json={**body, "device_id": "ex-aachen"})
        assert preview.status_code == 200 and preview.json()["config"] == {"city": "Aachen"}
        denied = await client.post("/module-config-templates/preview", json={**body, "device_id": "ex-duesseldorf"})
        assert denied.status_code == 403 and "Aachen only" in denied.json()["message"]
        assert (await client.post("/module-config-templates/preview", json={**body, "device_id": "nope"})).status_code == 404
        assert (await client.post("/module-config-templates/preview", json={
            "body": body["body"], "variables": [], "device_id": "ex-aachen"})).status_code == 422
        sample = await client.post("/module-config-templates/preview", json={**body, "device": {"city": "Sample"}})
        assert sample.json()["config"] == {"city": "Sample"}


class TestTechnicianPermissions:
    @pytest.fixture
    def fake_jwt_user(self):
        return {"oid": "tech-oid", "sub": "tech-oid", "preferred_username": "tech@test.com", "name": "Tech", "roles": []}

    async def test_example_devices_are_limited_to_the_users_scopes_too(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tech": "tech-oid"},
            scopes={"aachen": {"attr": {"city": "Aachen"}, "access_rule": "ALL"}},
            roles={"tech": ["device.read", "module_config.template.read"]},
            teams={"t": {"scope": "aachen", "roles": ["tech"], "users": ["tech"]}},
            devices={"ex-aachen": {"city": "Aachen"}, "ex-duesseldorf": {"city": "Duesseldorf"}},
        )
        from db.sqlalchemy.module_config import SQLAlchemyModuleConfigRepository
        group = await SQLAlchemyModuleConfigRepository(db_session).create_group(
            name="open", description=None, module_names=[MODULE], scope_ids=[], created_by="admin")
        listed = await client.get(f"/module-config-template-groups/{group['id']}/example-devices")
        assert [d["device_id"] for d in listed.json()] == ["ex-aachen"]
        body = {"body": {"city": "${device.city}"}, "variables": [], "group_id": group["id"]}
        assert (await client.post("/module-config-templates/preview", json={**body, "device_id": "ex-aachen"})).status_code == 200
        assert (await client.post("/module-config-templates/preview", json={**body, "device_id": "ex-duesseldorf"})).status_code == 404

    async def test_technician_applies_templates_but_cannot_write_raw(self, client, db_session, hub):
        await AbacFixtures(db_session).setup(
            users={"tech": "tech-oid"},
            roles={"tech": ["device.read", "device.module_config.apply", "module_config.template.read"]},
            teams={"t": {"scope": None, "roles": ["tech"], "users": ["tech"]}},
            devices={DEVICE: {"site": "Aachen"}},
        )
        from db.sqlalchemy.module_config import SQLAlchemyModuleConfigRepository
        repo = SQLAlchemyModuleConfigRepository(db_session)
        group = await repo.create_group(name="all", description=None, module_names=[MODULE], scope_ids=[], created_by="admin")
        template = await repo.create_template(
            name="t", description=None, group_id=group["id"], body={"a": "${a}"}, variables=[{"name": "a"}],
            comment=None, created_by="admin")

        assert (await client.post("/module-config-templates", json={**TEMPLATE, "group_id": group["id"]})).status_code == 403
        assert (await client.post("/module-config-template-groups", json={"name": "x", "module_names": ["m"]})).status_code == 403
        assert (await client.post(f"/{DEVICE}/twin/config/{MODULE}", json={"a": 1})).status_code == 403

        response = await client.post(f"/{DEVICE}/modules/{MODULE}/config/apply",
                                     json={"template_id": template["id"], "values": {"a": "x"}})
        assert response.status_code == 200, response.text
        assert response.json()["revision"]["actor"]

        hub["desired"][(DEVICE, MODULE)] = {"configId": "ext1", "configBlobUrl": "blob://ext1", "_blob": "ext1"}
        hub["blobs"][(DEVICE, MODULE, "ext1")] = {"raw": True}
        history = (await client.get(f"/{DEVICE}/modules/{MODULE}/config/revisions")).json()["revisions"]
        by_source = {r["source"]: r["id"] for r in history}
        assert (await client.post(
            f"/{DEVICE}/modules/{MODULE}/config/revisions/{by_source['external']}/reapply", json={})).status_code == 403
        assert (await client.post(
            f"/{DEVICE}/modules/{MODULE}/config/revisions/{by_source['template']}/reapply", json={})).status_code == 200


class TestTemplateGroups:
    async def test_crud_and_validation(self, client, db_session):
        world = await AbacFixtures(db_session).setup(scopes={"aachen": {"attr": {"city": "Aachen"}, "access_rule": "ALL"}})
        created = await _group(client, "Business Unit XYZ - OPC UA", ["seal-opcua-client"], [world.scopes["aachen"]])
        assert created["module_names"] == ["seal-opcua-client"] and created["template_count"] == 0
        assert [s["name"] for s in created["scopes"]] == [(await client.get("/auth/scopes")).json()[0]["name"]]

        duplicate = await client.post("/module-config-template-groups", json={"name": created["name"], "module_names": ["m"]})
        assert duplicate.status_code == 409
        unknown = await client.post("/module-config-template-groups", json={
            "name": "x", "module_names": ["m"], "scope_ids": [str(uuid.uuid4())]})
        assert unknown.status_code == 422

        for bad in ("seal-*", "seal-?", "*", "[a]lpha", "!seal", "-seal", "has space", ""):
            rejected = await client.post("/module-config-template-groups", json={"name": "w", "module_names": [bad]})
            assert rejected.status_code == 400, bad
        assert (await client.patch(f"/module-config-template-groups/{created['id']}", json={"module_names": ["seal-*"]})).status_code == 400

        updated = await client.patch(f"/module-config-template-groups/{created['id']}", json={
            "module_names": ["seal-opcua-client", "seal-mqtt"], "scope_ids": []})
        assert updated.json()["module_names"] == ["seal-opcua-client", "seal-mqtt"] and updated.json()["scopes"] == []
        assert (await client.get(f"/module-config-template-groups/{created['id']}")).json()["name"] == created["name"]
        assert (await client.get("/module-config-template-groups/999999")).status_code == 404

        template = await _create(client, group=created)
        assert template["group_id"] == created["id"] and template["group_name"] == created["name"]
        assert template["module_names"] == ["seal-opcua-client", "seal-mqtt"]
        assert (await client.get("/module-config-template-groups")).json()[0]["template_count"] == 1
        assert (await client.delete(f"/module-config-template-groups/{created['id']}")).status_code == 409

        other = await _group(client, "Other", ["seal-mqtt"])
        moved = await client.patch(f"/module-config-templates/{template['id']}", json={"group_id": other["id"]})
        assert moved.json()["group_name"] == "Other" and moved.json()["module_names"] == ["seal-mqtt"]
        assert (await client.patch(f"/module-config-templates/{template['id']}", json={"group_id": 999999})).status_code == 404
        assert (await client.delete(f"/module-config-template-groups/{created['id']}")).status_code == 204
        assert (await client.delete(f"/module-config-template-groups/{created['id']}")).status_code == 404

    async def test_templates_can_only_be_created_in_an_existing_group(self, client, db_session):
        response = await client.post("/module-config-templates", json={**TEMPLATE, "group_id": 999999})
        assert response.status_code == 404

    async def test_list_filters_by_group_modules_and_group(self, client, db_session):
        mqtt = await _group(client, "MQTT", ["seal-mqtt"])
        demo = await _group(client, "Demo", ["seal-demo-x"])
        await _create(client, group=mqtt, name="a")
        await _create(client, group=demo, name="b")
        names = lambda r: [t["name"] for t in r.json()]
        assert names(await client.get("/module-config-templates?module=seal-mqtt")) == ["a"]
        assert names(await client.get("/module-config-templates?module=seal-demo-x")) == ["b"]
        assert names(await client.get("/module-config-templates?module=seal-demo-xy")) == []
        assert names(await client.get(f"/module-config-templates?group_id={demo['id']}")) == ["b"]
        assert names(await client.get("/module-config-template-groups?module=seal-mqtt")) == ["MQTT"]
        assert names(await client.get("/module-config-template-groups")) == ["Demo", "MQTT"]

    async def test_duplicate_into_another_group(self, client, db_session):
        source = await _create(client)
        other = await _group(client, "Elsewhere", ["seal-demo-x"])
        copy = (await client.post(f"/module-config-templates/{source['id']}/duplicate",
                                  json={"name": "copy", "group_id": other["id"]})).json()
        assert copy["group_name"] == "Elsewhere" and copy["derived_from_template_id"] == source["id"]
        same = (await client.post(f"/module-config-templates/{source['id']}/duplicate", json={"name": "copy2"})).json()
        assert same["group_id"] == source["group_id"]

    async def test_scopes_decide_which_devices_may_use_a_group(self, client, db_session, hub):
        world = await AbacFixtures(db_session).setup(
            scopes={"aachen": {"attr": {"city": "Aachen"}, "access_rule": "ALL"}},
            devices={"ns-aachen": {"city": "Aachen", "site": "A"}, "ns-duesseldorf": {"city": "Duesseldorf", "site": "D"}},
        )
        scoped = await _group(client, "Aachen only", ["seal-demo-x"], [world.scopes["aachen"]])
        everyone = await _group(client, "Everyone", ["seal-demo-x"])
        private = await _create(client, group=scoped, name="private")
        shared = await _create(client, group=everyone, name="shared")

        async def available(device):
            response = await client.get(f"/{device}/modules/seal-demo-x/config/templates")
            assert response.status_code == 200, response.text
            return sorted(t["name"] for t in response.json())

        assert await available("ns-aachen") == ["private", "shared"]
        assert await available("ns-duesseldorf") == ["shared"]

        body = {"template_id": private["id"], "values": {"range": "10.0.0.0/24", "interval": 60}}
        denied = await client.post("/ns-duesseldorf/modules/seal-demo-x/config/render", json=body)
        assert denied.status_code == 403 and "Aachen only" in denied.json()["message"]
        assert (await client.post("/ns-duesseldorf/modules/seal-demo-x/config/apply", json=body)).status_code == 403
        assert not hub["blobs"]
        assert (await client.post("/ns-aachen/modules/seal-demo-x/config/render", json=body)).status_code == 200
        assert (await client.post("/ns-aachen/modules/seal-demo-x/config/apply", json=body)).status_code == 200
        shared_ok = await client.post("/ns-duesseldorf/modules/seal-demo-x/config/render",
                                      json={"template_id": shared["id"], "values": {"range": "x", "interval": 1}})
        assert shared_ok.status_code == 200

        # widening the scopes takes effect immediately
        await client.patch(f"/module-config-template-groups/{scoped['id']}", json={"scope_ids": []})
        assert await available("ns-duesseldorf") == ["private", "shared"]

    async def test_module_must_match_the_group(self, client, db_session, hub):
        await AbacFixtures(db_session).setup(devices={"ns-any": {"city": "x"}})
        template = await _create(client)
        response = await client.get("/ns-any/modules/seal-mqtt/config/templates")
        assert response.json() == []
        assert (await client.post("/ns-any/modules/seal-mqtt/config/render",
                                  json={"template_id": template["id"], "values": {}})).status_code == 422


async def _block(client, group, name, body, variables=(), kind="block"):
    response = await client.post("/module-config-templates", json={
        "name": name, "group_id": group["id"], "kind": kind, "body": body, "variables": list(variables)})
    assert response.status_code == 201, response.text
    return response.json()


def _ref(template):
    return {"$ref": template["name"]}


async def _skid(client):
    """Block `Items` (array) inside block `Client`, used twice by template `Skid`."""
    group = await _group(client)
    items = await _block(client, group, "Items", [{"name": "item"}])
    client_block = await _block(client, group, "Client", {"name": "${machine_name}", "items": _ref(items)},
                                [{"name": "machine_name"}])
    skid = await _block(client, group, "Skid", {"separatorA": _ref(client_block), "separatorB": _ref(client_block)},
                        kind="template")
    return group, client_block, skid


class TestBlocks:
    async def test_template_renders_blocks_and_apply_stores_prefixed_values(self, client, db_session, hub, device):
        _, _, skid = await _skid(client)
        assert skid["kind"] == "template"
        body = {"template_id": skid["id"], "values": {"separatorA/machine_name": "A", "separatorB/machine_name": "B"}}
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render", json=body)).json()
        assert render["ok"] is True, render
        assert render["config"] == {"separatorA": {"name": "A", "items": [{"name": "item"}]},
                                    "separatorB": {"name": "B", "items": [{"name": "item"}]}}
        response = await client.post(f"/{device}/modules/{MODULE}/config/apply", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["revision"]["values"] == body["values"]

    async def test_refs_are_checked_on_save(self, client, db_session):
        group, client_block, skid = await _skid(client)
        other = await _block(client, await _group(client, "other"), "Foreign", {"a": 1})

        async def codes(url, body):
            response = await client.post(url, json={"name": "new", "group_id": group["id"], "body": body, "variables": []})
            assert response.status_code == 422, response.text
            return {f["code"] for f in response.json()["findings"]}

        create = "/module-config-templates"
        assert "not-a-block" in await codes(create, {"a": _ref(skid)})
        assert "self-reference" in await codes(f"{create}/{client_block['id']}/versions", {"a": _ref(client_block)})
        assert "missing-block" in await codes(create, {"a": {"$ref": "Nope"}})
        assert "other-group" in await codes(create, {"a": _ref(other)})
        assert "body-not-object" in await codes(create, [1])

    async def test_blocks_are_not_offered_for_modules_and_archived_blocks_still_render(self, client, db_session, device):
        group, client_block, skid = await _skid(client)
        listed = (await client.get(f"/{device}/modules/{MODULE}/config/templates")).json()
        assert [t["name"] for t in listed] == ["Skid"]
        blocks = (await client.get(f"/module-config-templates?kind=block&group_id={group['id']}")).json()
        assert sorted(t["name"] for t in blocks) == ["Client", "Items"]
        assert (await client.delete(f"/module-config-templates/{client_block['id']}")).status_code == 200
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render", json={
            "template_id": skid["id"], "values": {"separatorA/machine_name": "A", "separatorB/machine_name": "B"}})).json()
        assert render["ok"] is True, render

    async def test_ref_follows_block_edits_and_renames(self, client, db_session, hub, device):
        group = await _group(client)
        block = await _block(client, group, "Block", {"a": 1})
        template = await _block(client, group, "T", {"b": _ref(block)}, kind="template")
        assert (await client.post(f"/module-config-templates/{block['id']}/versions", json={"body": {"a": 2}})).status_code == 201
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render", json={"template_id": template["id"]})).json()
        assert render["config"] == {"b": {"a": 2}}
        await client.patch(f"/module-config-templates/{block['id']}", json={"name": "Renamed"})
        version = (await client.get(f"/module-config-templates/{template['id']}/versions/latest")).json()
        assert version["body"] == {"b": {"$ref": "Renamed"}}

    async def test_an_older_version_using_a_block_stays_readable_after_its_template_moved(self, client, db_session):
        group = await _group(client)
        block = await _block(client, group, "Block", {"a": 1})
        template = await _block(client, group, "T", {"b": _ref(block)}, kind="template")
        base = f"/module-config-templates/{template['id']}"
        assert (await client.post(f"{base}/versions", json={"body": {"b": 2}})).status_code == 201
        moved = await client.patch(base, json={"group_id": (await _group(client, "elsewhere"))["id"]})
        assert moved.status_code == 200, moved.text
        old = (await client.get(f"{base}/versions/1")).json()
        assert old["body"] == {"b": {"$ref": "Block"}}

    async def test_a_row_that_uses_or_is_used_by_blocks_cannot_change_group(self, client, db_session):
        group, client_block, skid = await _skid(client)
        base = "/module-config-templates"
        elsewhere = await _group(client, "elsewhere")
        response = await client.patch(f"{base}/{skid['id']}", json={"group_id": elsewhere["id"]})
        assert response.status_code == 409
        assert "uses or is used by blocks" in response.text


async def _clients(client):
    """Template `Clients` with list `clients` of blocks `ClientSeparator` and `ClientDecanter`."""
    group = await _group(client)
    sep = await _block(client, group, "ClientSeparator",
                       {"endpoint": "opc.tcp://1.1.1.1:4840", "name": "${machine_name}"}, [{"name": "machine_name"}])
    dec = await _block(client, group, "ClientDecanter", {"endpoint": "opc.tcp://2.2.2.2:4840"})
    template = await _block(client, group, "Clients", {"clients": {"$list": ["ClientSeparator", "ClientDecanter"]}},
                            kind="template")
    return group, sep, dec, template


class TestLists:
    async def test_apply_with_list_entries_stores_them(self, client, db_session, hub, device):
        _, sep, dec, template = await _clients(client)
        entries = [{"name": "sep1", "block_id": sep["id"]}, {"name": "dec1", "block_id": dec["id"]}]
        body = {"template_id": template["id"], "values": {"clients/sep1/machine_name": "A"},
                "lists": {"clients": entries}}
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render", json=body)).json()
        assert render["ok"] is True, render
        assert render["config"] == {"clients": {"sep1": {"endpoint": "opc.tcp://1.1.1.1:4840", "name": "A"},
                                                "dec1": {"endpoint": "opc.tcp://2.2.2.2:4840"}}}
        response = await client.post(f"/{device}/modules/{MODULE}/config/apply", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["revision"]["values"]["$lists"] == {"clients": entries}
        version = (await client.get(f"/module-config-templates/{template['id']}/versions/latest")).json()
        assert version["body"] == {"clients": {"$list": ["ClientSeparator", "ClientDecanter"]}}

    async def test_list_entry_errors_are_422(self, client, db_session, hub, device):
        group, sep, _, template = await _clients(client)
        other = await _block(client, group, "Other", {"a": 1})

        async def codes(entries):
            response = await client.post(f"/{device}/modules/{MODULE}/config/apply", json={
                "template_id": template["id"], "values": {"clients/sep1/machine_name": "A"},
                "lists": {"clients": entries}})
            assert response.status_code == 422, response.text
            return {f["code"] for f in response.json()["findings"]}

        assert "duplicate-entry" in await codes([{"name": "sep1", "block_id": sep["id"]}] * 2)
        assert "block-not-allowed" in await codes([{"name": "x", "block_id": other["id"]}])

    async def test_list_follows_renames_and_archived_blocks_render(self, client, db_session, device):
        group, _, dec, template = await _clients(client)
        await client.patch(f"/module-config-templates/{dec['id']}", json={"name": "Decanter"})
        version = (await client.get(f"/module-config-templates/{template['id']}/versions/latest")).json()
        assert version["body"] == {"clients": {"$list": ["ClientSeparator", "Decanter"]}}
        assert (await client.delete(f"/module-config-templates/{dec['id']}")).status_code == 200
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render", json={
            "template_id": template["id"], "lists": {"clients": [{"name": "dec1", "block_id": dec["id"]}]}})).json()
        assert render["ok"] is True, render
        lint = (await client.post("/module-config-templates/lint", json={
            "body": version["body"], "group_id": group["id"]})).json()
        assert ("warning", "archived-block") in {(f["level"], f["code"]) for f in lint["findings"]}

    async def test_list_block_must_be_in_group(self, client, db_session):
        group = await _group(client)
        await _block(client, await _group(client, "other"), "Foreign", {"a": 1})
        response = await client.post("/module-config-templates", json={
            "name": "T", "group_id": group["id"], "body": {"clients": {"$list": ["Foreign"]}}, "variables": []})
        assert response.status_code == 422, response.text
        assert "other-group" in {f["code"] for f in response.json()["findings"]}

    async def test_list_entry_endpoint_slot_uses_the_chosen_endpoint(self, client, db_session, hub, device):
        group = await _group(client)
        block = await _block(client, group, "ClientEp", {"endpoint": "opc.tcp://${endpoint[machine].ip}:4840"})
        template = await _block(client, group, "ClientsEp", {"clients": {"$list": ["ClientEp"]}}, kind="template")
        type_id = await seed_endpoint_type(db_session, fields={"addr": {"type": "string", "required": False}},
                                           mapping={"addr": "ip"})
        endpoint_id = await seed_endpoint(db_session, device, type_id, {"addr": "10.1.2.3"})
        render = (await client.post(f"/{device}/modules/{MODULE}/config/render", json={
            "template_id": template["id"], "lists": {"clients": [{"name": "sep1", "block_id": block["id"]}]},
            "endpoints": {"clients/sep1/machine": endpoint_id}})).json()
        assert render["ok"] is True, render
        assert render["config"] == {"clients": {"sep1": {"endpoint": "opc.tcp://10.1.2.3:4840"}}}


class TestSlotDeclarations:
    async def test_slots_are_saved_with_the_version_and_copied_on_duplicate(self, client, db_session):
        plc = await seed_endpoint_type(db_session)
        slots = [{"name": "endpoint", "endpoint_types": [plc], "service_types": []}]
        template = await _create(client, name="restricted", body={"a": "${endpoint.ip}"}, variables=[], slots=slots)
        version = (await client.get(f"/module-config-templates/{template['id']}/versions/1")).json()
        assert version["slots"] == slots
        assert version["inputs"]["endpoints"][0]["endpoint_types"] == [plc]
        copy = (await client.post(f"/module-config-templates/{template['id']}/duplicate", json={"name": "copy"})).json()
        copied = (await client.get(f"/module-config-templates/{copy['id']}/versions/1")).json()
        assert copied["slots"] == slots

    async def test_unknown_type_ids_are_findings(self, client, db_session):
        group = await _group(client)
        create = lambda body, slots: client.post("/module-config-templates", json={
            **TEMPLATE, "name": "unknown-types", "group_id": group["id"], "body": body, "variables": [], "slots": slots})
        response = await create({"a": "${endpoint.ip}"},
                                [{"name": "endpoint", "endpoint_types": ["nope"], "service_types": []}])
        assert response.status_code == 422, response.text
        assert {"path": "/slots/0", "level": "error", "code": "unknown-endpoint-type"}.items() <= next(
            f for f in response.json()["findings"] if f["code"] == "unknown-endpoint-type").items()
        response = await create({"a": "${endpoint.ip}", "b": "${service.port}"},
                                [{"name": "endpoint", "endpoint_types": [], "service_types": ["nope"]}])
        assert response.status_code == 422, response.text
        assert {"path": "/slots/0", "level": "error", "code": "unknown-service-type"}.items() <= next(
            f for f in response.json()["findings"] if f["code"] == "unknown-service-type").items()

    async def test_version_without_slots_is_unrestricted(self, client, db_session):
        template = await _create(client, name="free", body={"a": "${endpoint.ip}"}, variables=[])
        version = (await client.get(f"/module-config-templates/{template['id']}/versions/1")).json()
        assert version["slots"] == []
        endpoint = version["inputs"]["endpoints"][0]
        assert endpoint["endpoint_types"] == [] and endpoint["service_types"] == []


class TestSlotTypeRestriction:
    async def _seed(self, db_session, device):
        """Endpoint types PLC/Modbus device, service types OPC UA/modbus-tcp (labels made unique, as the
        database may already hold such types); one endpoint of each type on the device, and one service of each
        service type on the PLC endpoint."""
        mapping = {"addr": "ip"}
        fields = {"addr": {"type": "string", "required": False}}
        repo, services = _endpoint_repo(db_session), _service_repo(db_session)
        plc_label, modbus_label = unique_label("PLC"), unique_label("Modbus device")
        opcua_label, tcp_label = unique_label("OPC UA"), unique_label("modbus-tcp")
        plc = (await repo.create_endpoint_type(plc_label, None, fields, mapping))["type_id"]
        modbus = (await repo.create_endpoint_type(modbus_label, None, fields, mapping))["type_id"]
        opcua = (await services.create_service_type(opcua_label, None, {"p": {"type": "integer", "required": False}},
                                                    mapping={"p": "port"}))["type_id"]
        tcp = (await services.create_service_type(tcp_label, None, {"p": {"type": "integer", "required": False}},
                                                  mapping={"p": "port"}))["type_id"]
        plc_ep = await seed_endpoint(db_session, device, plc, {"addr": "10.0.0.1"})
        modbus_ep = await seed_endpoint(db_session, device, modbus, {"addr": "10.0.0.2"})
        return {"plc_label": plc_label, "opcua_label": opcua_label, "plc": plc, "modbus": modbus, "opcua": opcua, "tcp": tcp, "plc_ep": plc_ep, "modbus_ep": modbus_ep,
                "opcua_svc": await seed_service(db_session, plc_ep, opcua, {"p": 4840}),
                "tcp_svc": await seed_service(db_session, plc_ep, tcp, {"p": 502})}

    async def _render(self, client, device, template, **body):
        return await client.post(f"/{device}/modules/{MODULE}/config/render", json={"template_id": template["id"], **body})

    async def test_restricted_slot_refuses_an_endpoint_of_another_type(self, client, db_session, device):
        seeded = await self._seed(db_session, device)
        template = await _create(client, name="r", body={"a": "${endpoint.ip}"}, variables=[],
                                 slots=[{"name": "endpoint", "endpoint_types": [seeded["plc"]], "service_types": []}])
        refused = await self._render(client, device, template, endpoints={"endpoint": seeded["modbus_ep"]})
        assert refused.status_code == 422
        assert refused.json()["message"] == f"Endpoint chosen for 'endpoint' must be of type {seeded['plc_label']}"
        allowed = await self._render(client, device, template, endpoints={"endpoint": seeded["plc_ep"]})
        assert allowed.status_code == 200 and allowed.json()["config"] == {"a": "10.0.0.1"}

    async def test_block_restriction_applies_to_its_copy_and_to_a_list_entry(self, client, db_session, device):
        seeded = await self._seed(db_session, device)
        group = await _group(client)
        block = await client.post("/module-config-templates", json={
            "name": "Client", "group_id": group["id"], "kind": "block", "body": {"url": "${endpoint.ip}"},
            "variables": [], "slots": [{"name": "endpoint", "endpoint_types": [seeded["plc"]], "service_types": []}]})
        assert block.status_code == 201, block.text
        block = block.json()
        copy = await _block(client, group, "A", {"client": _ref(block)}, kind="template")
        refused = await self._render(client, device, copy, endpoints={"client/endpoint": seeded["modbus_ep"]})
        assert refused.status_code == 422
        assert refused.json()["message"] == f"Endpoint chosen for 'client/endpoint' must be of type {seeded['plc_label']}"
        assert (await self._render(client, device, copy, endpoints={"client/endpoint": seeded["plc_ep"]})
                ).status_code == 200

        lst = await _block(client, group, "B", {"clients": {"$list": ["Client"]}}, kind="template")
        entries = {"clients": [{"name": "m1", "block_id": block["id"]}]}
        refused = await self._render(client, device, lst, lists=entries,
                                     endpoints={"clients/m1/endpoint": seeded["modbus_ep"]})
        assert refused.status_code == 422
        assert refused.json()["message"] == f"Endpoint chosen for 'clients/m1/endpoint' must be of type {seeded['plc_label']}"
        assert (await self._render(client, device, lst, lists=entries,
                                   endpoints={"clients/m1/endpoint": seeded["plc_ep"]})).status_code == 200

    async def test_restricted_slot_refuses_a_service_of_another_type(self, client, db_session, device):
        seeded = await self._seed(db_session, device)
        template = await _create(client, name="s", body={"a": "${endpoint.ip}:${service.port}"}, variables=[],
                                 slots=[{"name": "endpoint", "endpoint_types": [], "service_types": [seeded["opcua"]]}])
        chosen = {"endpoints": {"endpoint": seeded["plc_ep"]}}
        refused = await self._render(client, device, template, **chosen, services={"endpoint": seeded["tcp_svc"]})
        assert refused.status_code == 422
        assert refused.json()["message"] == f"Service chosen for 'endpoint' must be of type {seeded['opcua_label']}"
        allowed = await self._render(client, device, template, **chosen, services={"endpoint": seeded["opcua_svc"]})
        assert allowed.status_code == 200 and allowed.json()["config"] == {"a": "10.0.0.1:4840"}

    async def test_deleted_type_is_named_by_id(self, client, db_session, device):
        seeded = await self._seed(db_session, device)
        template = await _create(client, name="d", body={"a": "${endpoint.ip}"}, variables=[],
                                 slots=[{"name": "endpoint", "endpoint_types": [seeded["plc"]], "service_types": []}])
        await _endpoint_repo(db_session).delete_endpoint_type(seeded["plc"], cascade=True)
        refused = await self._render(client, device, template, endpoints={"endpoint": seeded["modbus_ep"]})
        assert refused.status_code == 422
        assert refused.json()["message"] == f"Endpoint chosen for 'endpoint' must be of type {seeded['plc']}"


class TestServices:
    async def test_apply_with_service_choice(self, client, db_session, hub, device):
        group = await _group(client)
        template = await _block(client, group, "Opc",
                                {"e": "opc.tcp://${endpoint[machine].ip}:${service[machine].port}"}, kind="template")
        ep_type = await seed_endpoint_type(db_session, fields={"addr": {"type": "string", "required": False}},
                                           mapping={"addr": "ip"})
        svc_type = await seed_service_type(db_session, fields={"p": {"type": "integer", "required": False}},
                                           mapping={"p": "port"})
        endpoint = await seed_endpoint(db_session, device, ep_type, {"addr": "10.0.0.1"})
        other = await seed_endpoint(db_session, device, ep_type, {"addr": "10.0.0.2"})
        svc = await seed_service(db_session, endpoint, svc_type, {"p": 4840})
        other_svc = await seed_service(db_session, other, svc_type, {"p": 4841})
        apply = f"/{device}/modules/{MODULE}/config/apply"
        body = {"template_id": template["id"], "endpoints": {"machine": endpoint}, "services": {"machine": svc}}
        response = await client.post(apply, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["revision"]["values"]["$services"] == {"machine": svc}
        assert hub["blobs"][(device, MODULE, response.json()["configId"])] == {"e": "opc.tcp://10.0.0.1:4840"}
        response = await client.post(apply, json={**body, "services": {"machine": other_svc}})
        assert response.status_code == 422, response.text
        listed = (await client.get(f"/{device}/config/endpoints/{endpoint}/services")).json()
        assert [(s["service_id"], s["port"]) for s in listed] == [(svc, 4840)]

    async def test_list_label_survives_save(self, client, db_session):
        group, _, _, _ = await _clients(client)
        template = await _block(client, group, "Labeled", {"s": {"$list": ["ClientSeparator"], "$label": "Slave"}},
                                kind="template")
        version = (await client.get(f"/module-config-templates/{template['id']}/versions/latest")).json()
        assert version["body"] == {"s": {"$list": ["ClientSeparator"], "$label": "Slave"}}
        assert version["inputs"]["lists"][0]["item_label"] == "Slave"


class TestRows:
    async def test_apply_with_a_row_choice(self, client, db_session, hub, device):
        group = await _group(client)
        template = await _block(client, group, "Machine",
                                {"id": "${endpoint.equipment.identifier}", "crm": "${endpoint.equipment.crm_id}"},
                                kind="template")
        ep_type = await seed_endpoint_type(db_session, fields={"equipment": {
            "type": "list", "item_fields": {"identifier": {"type": "string"}}}})
        rows = [{"id": "r1", "identifier": "10330", "crm_id": "21289"}, {"id": "r2", "identifier": "41234", "crm_id": "7"}]
        many = await seed_endpoint(db_session, device, ep_type, {"equipment": rows})
        one = await seed_endpoint(db_session, device, ep_type, {"equipment": rows[:1]})
        apply = f"/{device}/modules/{MODULE}/config/apply"

        # several rows and no choice: findings, nothing written
        response = await client.post(apply, json={"template_id": template["id"], "endpoints": {"endpoint": many}})
        assert response.status_code == 422, response.text
        assert {f["code"] for f in response.json()["findings"]} == {"missing-row"}

        body = {"template_id": template["id"], "endpoints": {"endpoint": many}, "rows": {"endpoint": {"equipment": "r2"}}}
        response = await client.post(apply, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["revision"]["values"]["$rows"] == {"endpoint": {"equipment": "r2"}}
        assert hub["blobs"][(device, MODULE, response.json()["configId"])] == {"id": "41234", "crm": "7"}

        # a row id of another endpoint
        response = await client.post(apply, json={**body, "rows": {"endpoint": {"equipment": "nope"}}})
        assert response.status_code == 422, response.text
        # the body key depends on the app's HTTPException handler, so match the text
        assert "Row chosen for 'endpoint' in 'equipment' is not on the chosen endpoint" in response.text

        # a row id for an endpoint without rows: the renderer's finding, not the 422 above
        empty = await seed_endpoint(db_session, device, ep_type, {})
        response = await client.post(apply, json={"template_id": template["id"], "endpoints": {"endpoint": empty},
                                                  "rows": {"endpoint": {"equipment": "r1"}}})
        assert response.status_code == 422, response.text
        assert {f["code"] for f in response.json()["findings"]} == {"missing-endpoint-field"}
        assert "Row chosen for" not in response.text

        # one row: used without a choice
        response = await client.post(apply, json={"template_id": template["id"], "endpoints": {"endpoint": one}})
        assert response.status_code == 200, response.text
        assert hub["blobs"][(device, MODULE, response.json()["configId"])] == {"id": "10330", "crm": "21289"}
        assert "$rows" not in response.json()["revision"]["values"]


class TestCertificateFiles:
    async def test_render_and_preview_mask_secret_files(self, client, db_session, hub, device, monkeypatch):
        monkeypatch.setenv("FILE_ENCRYPTION_KEY", Fernet.generate_key().decode())
        group = await _group(client)
        fields = {"cert": {"type": "file", "label": "Cert"}, "key": {"type": "file", "label": "Key", "secret": True}}
        endpoint_id = await seed_endpoint(db_session, device, await seed_endpoint_type(db_session, fields))
        stream = {"content-type": "application/octet-stream"}
        cert = make_cert()
        for field, name, content in (("cert", "client.crt", cert), ("key", "client.key", make_key())):
            put = await client.put(f"/endpoints/{endpoint_id}/files/{field}", params={"name": name}, content=content,
                                   headers=stream)
            assert put.status_code == 200, put.text
        body = {"serverCertificate": "${endpoint.cert}", "clientPrivateKey": "${endpoint.key}"}
        template = await _block(client, group, "Tls", body, kind="template")

        render = await client.post(f"/{device}/modules/{MODULE}/config/render", json={
            "template_id": template["id"], "endpoints": {"endpoint": endpoint_id}})
        preview = await client.post("/module-config-templates/preview", json={
            "body": body, "variables": [], "group_id": group["id"], "device_id": device,
            "endpoints": {"endpoint": endpoint_id}})
        for response in (render, preview):
            assert response.status_code == 200, response.text
            assert "PRIVATE KEY" not in response.text
            config = response.json()["config"]
            assert config["serverCertificate"] == cert.decode()
            assert config["clientPrivateKey"].startswith("<secret: client.key #")
