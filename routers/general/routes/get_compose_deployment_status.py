import logging

import yaml

from db.repos.compose import ComposeRepository
from exceptions import SEMSError
from smart_ems import SmartEMS

logger = logging.getLogger("EdgeConfigAPI")


def _same_compose(a: str, b: str) -> bool:
    try:
        return yaml.safe_load(a) == yaml.safe_load(b)
    except yaml.YAMLError:
        return False


async def get_compose_deployment_status(device_id: str, repo: ComposeRepository):
    """
    Status of the active compose deployment on one device.

    The compose deployment is pushed to SEMS as a variable for all devices, so "applied" means the
    device's generated SEMS config already contains it. "success" additionally requires that no
    config update is still waiting to be installed on the device.
    """
    active = await repo.get_active_deployment()
    if not active:
        return {"deviceId": device_id}

    deployment = await repo.get(active)
    expected = (deployment or {}).get("sems_compose") or {}

    applied = False
    config_update_pending = True
    try:
        device_info = await SmartEMS.get_device_by_serial(device_id, require_template=False)
        if device_info is not None and expected:
            config = await SmartEMS.device_config_download(device_info["id"])
            files = (config.get("docker") or {}).get("compose_files") or {}
            applied = all(name in files and _same_compose(files[name], content) for name, content in expected.items())
            config_update_pending = bool(device_info.get("reinstallConfig1", False))
    except SEMSError as error:
        logger.info(f"Could not read compose deployment status for device <{device_id}>: {error}")

    return {
        "deviceId": device_id,
        "deploymentId": active,
        "applied": applied,
        "success": applied and not config_update_pending,
    }
