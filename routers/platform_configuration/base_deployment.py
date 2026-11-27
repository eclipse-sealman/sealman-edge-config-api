from typing import Any, Dict, List, Optional

from bootstrap.base_deployment_manifest import (
    BaseDeploymentError,
    default_edge_hub_env,
    default_modules,
    render_manifest,
    render_settings,
)
from db.repos.base_deployment import BaseDeploymentRepository
from db.repos.device_template import DeviceTemplateRepository
from exceptions import APIError


async def get_base_deployment(
    template_repo: DeviceTemplateRepository, repo: BaseDeploymentRepository
) -> Dict[str, Any]:
    stored = await repo.get()
    if stored:
        return {
            "modules": stored.modules,
            "edge_hub_env": stored.edge_hub_env,
            "manifest": stored.manifest,
            "stored": True,
        }
    settings = render_settings(await template_repo.get_template_variables())
    modules = default_modules(settings)
    edge_hub_env = default_edge_hub_env(settings)
    return {
        "modules": modules,
        "edge_hub_env": edge_hub_env,
        "manifest": render_manifest(modules, settings, edge_hub_env),
        "stored": False,
    }


async def save_base_deployment(
    modules: List[dict],
    edge_hub_env: Optional[Dict[str, str]],
    template_repo: DeviceTemplateRepository,
    repo: BaseDeploymentRepository,
) -> Dict[str, Any]:
    """`edge_hub_env` None keeps the saved edgeHub environment (or the shipped default)."""
    settings = render_settings(await template_repo.get_template_variables())
    if edge_hub_env is None:
        stored = await repo.get()
        edge_hub_env = stored.edge_hub_env if stored else default_edge_hub_env(settings)
    try:
        manifest = render_manifest(modules, settings, edge_hub_env)
    except BaseDeploymentError as ex:
        raise APIError(str(ex), 400)
    await repo.save(modules, manifest, edge_hub_env)
    return {"modules": modules, "edge_hub_env": edge_hub_env, "manifest": manifest, "stored": True}


async def rerender_stored_base_deployment(
    template_repo: DeviceTemplateRepository, repo: BaseDeploymentRepository
) -> None:
    """Keeps registry credentials and the agent image of a saved manifest in step with the platform variables."""
    stored = await repo.get()
    if stored:
        await save_base_deployment(stored.modules, stored.edge_hub_env, template_repo, repo)
