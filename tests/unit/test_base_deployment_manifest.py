import json

import pytest

from bootstrap.base_deployment_manifest import (
    BaseDeploymentError,
    default_edge_hub_env,
    default_modules,
    render_manifest,
    render_settings,
)

VARIABLES = {
    "iotedge_registry_url": "reg.example.com",
    "iotedge_registry_username": "user",
    "iotedge_registry_password": 'p"w\\d',
    "edge_agent_image": "reg.example.com/agent:9",
}


def _module(**overrides):
    return {
        "name": "my-mod",
        "image": "reg.example.com/my:1",
        "restart_policy": "always",
        "status": "running",
        "create_options": "",
        "env": {},
        "twin": {},
        **overrides,
    }


def _desired(manifest):
    return manifest["content"]["modulesContent"]["$edgeAgent"]["properties.desired"]


def test_default_modules_match_the_shipped_template():
    modules = default_modules(render_settings(VARIABLES))

    assert [m["name"] for m in modules] == ["seal-mqtt", "seal-net-discover", "seal-cmd-proxy"]
    assert modules[0]["image"] == "reg.example.com/seal-app-mqtt:1.2.0"
    assert json.loads(modules[0]["create_options"])["HostConfig"]["PortBindings"]["1883/tcp"]


def test_default_modules_render_back_to_the_template_modules():
    settings = render_settings(VARIABLES)
    manifest = render_manifest(default_modules(settings), settings)

    mqtt = _desired(manifest)["modules"]["seal-mqtt"]
    assert mqtt["settings"]["image"] == "reg.example.com/seal-app-mqtt:1.2.0"
    assert mqtt["restartPolicy"] == "always" and mqtt["status"] == "running"


def test_credentials_are_escaped_and_system_modules_are_kept():
    settings = render_settings(VARIABLES)
    desired = _desired(render_manifest([_module()], settings))

    creds = desired["runtime"]["settings"]["registryCredentials"]["seal-acr"]
    assert creds["password"] == 'p"w\\d'
    assert desired["systemModules"]["edgeAgent"]["settings"]["image"] == "reg.example.com/agent:9"
    assert "edgeHub" in desired["systemModules"]


def test_env_create_options_and_twin_are_rendered():
    settings = render_settings(VARIABLES)
    manifest = render_manifest(
        [_module(env={"A": "1"}, create_options='{ "Tty": true }', twin={"interval": 5})], settings
    )

    module = _desired(manifest)["modules"]["my-mod"]
    assert module["env"] == {"A": {"value": "1"}}
    assert module["settings"]["createOptions"] == '{"Tty":true}'
    assert manifest["content"]["modulesContent"]["my-mod"] == {"properties.desired": {"interval": 5}}


@pytest.mark.parametrize("module", [
    _module(name="edgeAgent"),
    _module(name="bad name"),
    _module(image=" "),
    _module(create_options="{oops"),
    _module(create_options="[]"),
    _module(restart_policy="sometimes"),
    _module(twin=[]),
])
def test_invalid_modules_are_rejected(module):
    with pytest.raises(BaseDeploymentError):
        render_manifest([module], render_settings(VARIABLES))


def test_duplicate_module_names_are_rejected():
    with pytest.raises(BaseDeploymentError):
        render_manifest([_module(), _module()], render_settings(VARIABLES))


def test_edge_hub_env_defaults_come_from_the_template():
    assert default_edge_hub_env(render_settings(VARIABLES)) == {
        "MaxUpstreamBatchSize": "100",
        "RuntimeLogLevel": "warning",
        "UpstreamProtocol": "AmqpWs",
    }


def test_edge_hub_env_replaces_the_template_env():
    settings = render_settings(VARIABLES)
    hub = _desired(render_manifest([_module()], settings, {"RuntimeLogLevel": "debug"}))["systemModules"]["edgeHub"]

    assert hub["env"] == {"RuntimeLogLevel": {"value": "debug"}}
    assert hub["settings"]["image"]


def test_edge_hub_env_names_must_not_be_empty():
    with pytest.raises(BaseDeploymentError):
        render_manifest([_module()], render_settings(VARIABLES), {" ": "x"})
