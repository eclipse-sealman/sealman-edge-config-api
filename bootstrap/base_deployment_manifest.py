"""Rendering of the IoT Hub base deployment manifest from an editable list of modules."""

import json
import re
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

from constants import (
    BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
    BOOTSTRAP_IOTHUB_TARGET_CONDITION,
    BOOTSTRAP_IOTHUB_PRIORITY,
    CONTAINER_REGISTRY_ADDRESS,
    CONTAINER_REGISTRY_USERNAME,
    CONTAINER_REGISTRY_PASSWORD,
    EDGE_AGENT_IMAGE,
    EDGE_HUB_IMAGE,
)

_MANIFEST_FILE = Path(__file__).parent / "seal-base-deployment.json"

RESTART_POLICIES = ("always", "never", "on-failure", "on-unhealthy")
MODULE_STATUSES = ("running", "stopped")
MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RESERVED_MODULE_NAMES = frozenset({"edgeagent", "edgehub"})

# Template variables whose change alters the rendered manifest (registry credentials, agent image).
MANIFEST_VARIABLES = frozenset({
    "iotedge_registry_url",
    "iotedge_registry_username",
    "iotedge_registry_password",
    "edge_agent_image",
})


class BaseDeploymentError(ValueError):
    pass


def render_settings(variables: Dict[str, str]) -> Dict[str, Any]:
    """Values substituted into the manifest template: platform variables first, then the environment."""
    return {
        "deployment_name": BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME,
        "target_condition": BOOTSTRAP_IOTHUB_TARGET_CONDITION,
        "priority": BOOTSTRAP_IOTHUB_PRIORITY,
        "container_registry_address": variables.get("iotedge_registry_url") or CONTAINER_REGISTRY_ADDRESS or "docker.io",
        "container_registry_username": variables.get("iotedge_registry_username") or CONTAINER_REGISTRY_USERNAME,
        "container_registry_password": variables.get("iotedge_registry_password") or CONTAINER_REGISTRY_PASSWORD,
        "edge_agent_image": variables.get("edge_agent_image") or EDGE_AGENT_IMAGE or "mcr.microsoft.com/azureiotedge-agent:1.4",
        "edge_hub_image": EDGE_HUB_IMAGE or "mcr.microsoft.com/azureiotedge-hub:1.4",
    }


def _template_body(settings: Dict[str, Any]) -> dict:
    # Strings are JSON-escaped so credentials with quotes or backslashes cannot break the document.
    escaped = {k: json.dumps(v)[1:-1] if isinstance(v, str) else v for k, v in settings.items()}
    return json.loads(Template(_MANIFEST_FILE.read_text()).substitute(escaped))


def default_modules(settings: Dict[str, Any]) -> List[dict]:
    """The modules shipped in the manifest template, in the editable form."""
    body = _template_body(settings)
    modules_content = body["content"]["modulesContent"]
    desired = modules_content["$edgeAgent"]["properties.desired"]
    modules = []
    for name, module in desired["modules"].items():
        modules.append({
            "name": name,
            "image": module["settings"]["image"],
            "restart_policy": module.get("restartPolicy", "always"),
            "status": module.get("status", "running"),
            "create_options": module["settings"].get("createOptions", ""),
            "env": {key: str(value.get("value", "")) for key, value in module.get("env", {}).items()},
            "twin": modules_content.get(name, {}).get("properties.desired", {}),
        })
    return modules


def _compact_create_options(value: str) -> str:
    return json.dumps(json.loads(value), separators=(",", ":"))


def default_edge_hub_env(settings: Dict[str, Any]) -> Dict[str, str]:
    """The edgeHub environment variables shipped in the manifest template."""
    hub = _template_body(settings)["content"]["modulesContent"]["$edgeAgent"]["properties.desired"]["systemModules"]["edgeHub"]
    return {key: str(value.get("value", "")) for key, value in hub.get("env", {}).items()}


def validate_edge_hub_env(env: Dict[str, str]) -> None:
    if not all(key.strip() for key in env):
        raise BaseDeploymentError("edgeHub: environment variable names must not be empty")


def validate_modules(modules: List[dict]) -> None:
    names = set()
    for module in modules:
        name = module["name"]
        if not MODULE_NAME_PATTERN.match(name):
            raise BaseDeploymentError(f"Invalid module name '{name}'")
        if name.lower() in RESERVED_MODULE_NAMES:
            raise BaseDeploymentError(f"'{name}' is a system module and cannot be changed here")
        if name in names:
            raise BaseDeploymentError(f"Module name '{name}' is used twice")
        names.add(name)
        if not module["image"].strip():
            raise BaseDeploymentError(f"Module '{name}' needs an image")
        if module["restart_policy"] not in RESTART_POLICIES:
            raise BaseDeploymentError(f"Module '{name}': invalid restart policy")
        if module["status"] not in MODULE_STATUSES:
            raise BaseDeploymentError(f"Module '{name}': invalid status")
        if module["create_options"].strip():
            try:
                options = json.loads(module["create_options"])
            except ValueError:
                raise BaseDeploymentError(f"Module '{name}': createOptions is not valid JSON")
            if not isinstance(options, dict):
                raise BaseDeploymentError(f"Module '{name}': createOptions must be a JSON object")
        if not all(key.strip() for key in module["env"]):
            raise BaseDeploymentError(f"Module '{name}': environment variable names must not be empty")
        if not isinstance(module["twin"], dict):
            raise BaseDeploymentError(f"Module '{name}': twin must be a JSON object")


def render_manifest(modules: List[dict], settings: Dict[str, Any], edge_hub_env: Optional[Dict[str, str]] = None) -> dict:
    """The complete IoT Hub deployment document: system modules from the template plus the given modules.

    `edge_hub_env` replaces the edgeHub environment of the template; None keeps the template's.
    """
    validate_modules(modules)
    body = _template_body(settings)
    modules_content = body["content"]["modulesContent"]
    desired = modules_content["$edgeAgent"]["properties.desired"]
    desired["modules"] = {}

    if edge_hub_env is not None:
        validate_edge_hub_env(edge_hub_env)
        hub = desired["systemModules"]["edgeHub"]
        hub["env"] = {key.strip(): {"value": value} for key, value in edge_hub_env.items()}

    for module in modules:
        definition: Dict[str, Any] = {
            "type": "docker",
            "status": module["status"],
            "restartPolicy": module["restart_policy"],
            "settings": {"image": module["image"].strip()},
        }
        if module["create_options"].strip():
            definition["settings"]["createOptions"] = _compact_create_options(module["create_options"])
        if module["env"]:
            definition["env"] = {key: {"value": value} for key, value in module["env"].items()}
        desired["modules"][module["name"]] = definition
        if module["twin"]:
            modules_content[module["name"]] = {"properties.desired": module["twin"]}

    return body
