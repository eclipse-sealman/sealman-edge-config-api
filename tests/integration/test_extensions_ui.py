"""Integration tests for the `ui` section of the extension manifest."""
from uuid import uuid4

import pytest
from sqlalchemy import delete

from db.models.extension import Extension as ExtensionModel
from tests.integration.test_abac_authorization import AbacFixtures


def _name() -> str:
    return f"ui_ext_{uuid4().hex[:8]}"


def _section(**overrides) -> dict:
    return {"target": "endpoint", "placement": "section", "module": "./Panel", "label": "Panel", **overrides}


def _tab(label: str, **overrides) -> dict:
    return {"target": "device", "placement": "tab", "module": "./Tab", "label": label, **overrides}


def _replace(**overrides) -> dict:
    return {"target": "module", "placement": "replace:config", "module": "./Config", "label": "Config", **overrides}


def _ui(entry="/ext-ui/crm/remoteEntry.js", module=None, target=None, remote_name="crm", contributions=None) -> dict:
    if contributions is None:
        contribution = _section()
        if module is not None:
            contribution["module"] = module
        if target is not None:
            contribution["target"] = target
        contributions = [contribution]
    return {"contractVersion": 1, "entry": entry, "remoteName": remote_name, "contributions": contributions}


def _manifest(ui=None) -> dict:
    name = _name()
    manifest = {
        "schema_version": 1,
        "name": name,
        "upstreams": {"svc": {"type": "http", "base_url": "http://localhost:9000"}},
        "routes": [{"upstream": "svc", "path": f"/{name}/ping", "upstream_path": "/ping"}],
    }
    if ui is not None:
        manifest["ui"] = ui
    return manifest


class TestExtensionUiManifest:
    async def test_ui_entry_must_be_same_origin_path(self, client):
        for entry in ["https://evil.example/remoteEntry.js", "//evil.example/r.js", "/a\\b.js", "remoteEntry.js",
                      "/r?v=1.js", "/r#x.js", "/a b.js", "/a\tb.js"]:
            r = await client.post("/extensions", json=_manifest(ui=_ui(entry=entry)))
            assert r.status_code == 422, entry

    async def test_ui_formats_validated_unknown_target_accepted(self, client):
        assert (await client.post("/extensions", json=_manifest(ui=_ui(module="EndpointPanel")))).status_code == 422
        assert (await client.post("/extensions", json=_manifest(ui=_ui(target="Bad Target")))).status_code == 422
        assert (await client.post("/extensions", json=_manifest(ui=_ui(remote_name="1crm")))).status_code == 422
        blank = _ui(contributions=[_section(label="   ")])
        assert (await client.post("/extensions", json=_manifest(ui=blank))).status_code == 422
        dup = _ui(contributions=[_tab("a", path="x"), _tab("b", path="x")])
        assert (await client.post("/extensions", json=_manifest(ui=dup))).status_code == 422
        unknown_action = _ui(contributions=[_section(requiredAction="nope.unknown")])
        assert (await client.post("/extensions", json=_manifest(ui=unknown_action))).status_code == 422
        remote = f"crm{uuid4().hex[:8]}"
        action = _section(requiredAction=f"{_name()}.read")
        manifest = _manifest(ui=_ui(target="future-thing", remote_name=remote))
        manifest["actions"] = [{"name": action["requiredAction"]}]
        manifest["ui"]["contributions"].append(action)
        r = await client.post("/extensions", json=manifest)
        assert r.status_code == 201
        ui = r.json()["ui"]
        assert ui["contributions"][0]["target"] == "future-thing"
        assert ui["contractVersion"] == 1 and ui["remoteName"] == remote
        assert ui["contributions"][1]["requiredAction"] == action["requiredAction"]
        clash = await client.post("/extensions", json=_manifest(ui=_ui(remote_name=remote)))
        assert clash.status_code == 409
        assert r.json()["name"] in clash.json()["message"]
        await client.delete(f"/extensions/{r.json()['name']}")

    async def test_replace_conflicts(self, client):
        def make(remote_name, modules):
            return _manifest(ui=_ui(remote_name=remote_name, contributions=[_replace(when={"moduleType": modules})]))

        a, b, c = make("rc_a", ["opcua"]), make("rc_b", ["opcua", "modbus"]), make("rc_c", ["modbus"])
        try:
            for m in (a, b, c):
                assert (await client.post("/extensions", json=m)).status_code == 201
            assert (await client.post(f"/extensions/{a['name']}/enable")).status_code == 200
            assert (await client.post(f"/extensions/{b['name']}/enable")).status_code == 409  # overlaps a
            assert (await client.post(f"/extensions/{c['name']}/enable")).status_code == 200  # disjoint
            assert (await client.post(f"/extensions/{a['name']}/disable")).status_code == 200
            assert (await client.post(f"/extensions/{b['name']}/enable")).status_code == 409  # still overlaps c on modbus
        finally:
            for m in (a, b, c):
                await client.delete(f"/extensions/{m['name']}")


async def _register(client, remote_name, ui=None, enable=True, actions=None):
    manifest = _manifest(ui=ui)
    if actions:
        manifest["actions"] = actions
    assert (await client.post("/extensions", json=manifest)).status_code == 201
    if enable:
        assert (await client.post(f"/extensions/{manifest['name']}/enable")).status_code == 200
    return manifest["name"]


def _gated_ui(remote_name):
    return _ui(
        remote_name=remote_name,
        contributions=[_section(label="Plain"), _section(label="Gated", requiredAction="device.metadata.write")],
    )


class TestExtensionUiList:
    async def test_ui_list_only_enabled_and_only_ui_fields(self, client):
        sfx = uuid4().hex[:6]
        names = []
        try:
            on = await _register(client, f"on{sfx}", _ui(remote_name=f"on{sfx}")); names.append(on)
            off = await _register(client, f"off{sfx}", _ui(remote_name=f"off{sfx}"), enable=False); names.append(off)
            noui = await _register(client, f"noui{sfx}"); names.append(noui)

            r = await client.get("/extensions/ui")
            assert r.status_code == 200
            listed = [e["name"] for e in r.json()]
            assert on in listed and off not in listed and noui not in listed
            entry = next(e for e in r.json() if e["name"] == on)
            assert set(entry) == {"name", "contractVersion", "entry", "remoteName", "contributions"}
            assert r.headers["cache-control"] == "private, max-age=60" and r.headers["etag"]
            again = await client.get("/extensions/ui", headers={"If-None-Match": r.headers["etag"]})
            assert again.status_code == 304
        finally:
            for n in names:
                await client.delete(f"/extensions/{n}")


async def _insert_enabled(db_session, remote_name):
    """Non-admin users cannot register extensions, so the row is inserted directly."""
    name = _name()
    ui = _gated_ui(remote_name)
    db_session.add(ExtensionModel(name=name, description="", schema_version=1, enabled=True, ui=ui))
    await db_session.commit()
    return name


async def _drop(db_session, name):
    await db_session.execute(delete(ExtensionModel).where(ExtensionModel.name == name))
    await db_session.commit()


class TestExtensionUiListAsPlainUser:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "extensions-test-oid",
            "sub": "extensions-test-oid",
            "preferred_username": "extensions-test@test.com",
            "name": "Extensions Test",
            "roles": [],
        }

    async def test_ui_list_for_non_admin_filters_required_action(self, client, db_session):
        await AbacFixtures(db_session).setup(
            users={"tester": "extensions-test-oid"},
            roles={"reader": ["device.read"]},
            teams={"t": {"roles": ["reader"], "users": ["tester"]}},
        )
        sfx = uuid4().hex[:6]
        name = await _insert_enabled(db_session, f"gate{sfx}")
        try:
            r = await client.get("/extensions/ui")
            assert r.status_code == 200
            labels = [c["label"] for e in r.json() if e["name"] == name for c in e["contributions"]]
            assert labels == ["Plain"]
        finally:
            await _drop(db_session, name)

    async def test_ui_list_for_user_without_teams(self, client, db_session):
        sfx = uuid4().hex[:6]
        name = await _insert_enabled(db_session, f"nt{sfx}")
        try:
            r = await client.get("/extensions/ui")
            assert r.status_code == 200
            assert all(c.get("requiredAction") is None for e in r.json() for c in e["contributions"])
            assert [c["label"] for e in r.json() if e["name"] == name for c in e["contributions"]] == ["Plain"]
        finally:
            await _drop(db_session, name)
