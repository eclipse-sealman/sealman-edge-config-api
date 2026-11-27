"""Base deployment: default, saving the rendered manifest in the DB, validation and reset."""


def _module(**overrides):
    return {"name": "custom-mod", "image": "reg.example.com/custom:1", **overrides}


def _desired(manifest):
    return manifest["content"]["modulesContent"]["$edgeAgent"]["properties.desired"]


async def test_default_is_shown_until_saved(client):
    body = (await client.get("/platform/base-deployment")).json()

    assert body["stored"] is False
    assert [m["name"] for m in body["modules"]] == ["seal-mqtt", "seal-net-discover", "seal-cmd-proxy"]
    assert body["manifest"]["id"] == "seal-base-deployment"


async def test_saved_manifest_is_stored_rendered(client):
    modules = [_module(env={"A": "1"}, twin={"x": None, "y": 2})]

    saved = await client.put("/platform/base-deployment", json={"modules": modules})
    loaded = (await client.get("/platform/base-deployment")).json()

    assert saved.status_code == 200, saved.text
    assert loaded["stored"] is True
    assert list(_desired(loaded["manifest"])["modules"]) == ["custom-mod"]
    assert _desired(loaded["manifest"])["modules"]["custom-mod"]["env"] == {"A": {"value": "1"}}
    assert loaded["manifest"]["content"]["modulesContent"]["custom-mod"] == {"properties.desired": {"x": None, "y": 2}}
    assert loaded["modules"][0]["twin"] == {"x": None, "y": 2}


async def test_registry_variable_change_rerenders_saved_manifest(client, monkeypatch):
    import routers.platform_configuration.router as platform_router

    async def _noop(*args, **kwargs):
        return True

    monkeypatch.setattr(platform_router.SmartEMS, "set_variable_for_all_devices", _noop)
    await client.put("/platform/base-deployment", json={"modules": [_module()]})

    await client.put("/platform/device-template-variables/iotedge_registry_username", json={"value": "new-user"})

    manifest = (await client.get("/platform/base-deployment")).json()["manifest"]
    creds = _desired(manifest)["runtime"]["settings"]["registryCredentials"]["seal-acr"]
    assert creds["username"] == "new-user"


async def test_edge_hub_env_is_editable_and_kept_when_omitted(client):
    default = (await client.get("/platform/base-deployment")).json()
    assert default["edge_hub_env"]["UpstreamProtocol"] == "AmqpWs"

    await client.put("/platform/base-deployment", json={"modules": [_module()], "edge_hub_env": {"RuntimeLogLevel": "debug"}})
    await client.put("/platform/base-deployment", json={"modules": [_module(name="other")]})

    loaded = (await client.get("/platform/base-deployment")).json()
    assert loaded["edge_hub_env"] == {"RuntimeLogLevel": "debug"}
    hub = _desired(loaded["manifest"])["systemModules"]["edgeHub"]
    assert hub["env"] == {"RuntimeLogLevel": {"value": "debug"}}


async def test_invalid_modules_are_rejected(client):
    response = await client.put("/platform/base-deployment", json={"modules": [_module(name="edgeAgent")]})

    assert response.status_code == 400
    assert (await client.get("/platform/base-deployment")).json()["stored"] is False


async def test_reset_returns_to_default(client):
    await client.put("/platform/base-deployment", json={"modules": [_module()]})

    reset = await client.delete("/platform/base-deployment")

    assert reset.status_code == 200
    assert reset.json()["stored"] is False
    assert len(reset.json()["modules"]) == 3
