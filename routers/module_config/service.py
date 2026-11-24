"""
Single write path for module configurations.

Every writer (template apply, raw editor, legacy typed endpoints, extension `twin_write`,
re-apply) goes through `write_module_config`: the content is uploaded to the immutable blob
`iotedge-device-twin/{device}/{module}/{configId}`, the module twin's desired properties get
only `configBlobUrl` + `configId` (the device contract is unchanged) and an audited revision is
recorded. Configurations written by other means are picked up lazily by `sync_external`.

A revision rendered from secret files stores their JSON pointers (`secret_paths`). Writers of a
configuration supplied by a caller (raw editor, typed legacy endpoints, extension `twin_write`) go
through `write_supplied_config`, which turns the masks of the current revision back into the files.
"""
import hashlib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from async_requests import patch_async
from blob_service import BlobContainerContext, blob_url
from constants import (
    BLOB_SAS_TOKEN_MODULE_CONF, IOT_HUB_NAME, MODULE_CONFIG_DEVICE_STORAGE_URL, MODULE_CONFIG_STORAGE_URL,
    PUBLIC_STORAGE_ACCOUNT_NAME,
)
from db.repos.field_file import FieldFileRepository
from db.repos.module_config import ModuleConfigRepository
from exceptions import IoTBackendAPIError, UploadError
from helper import encode_iothub_id, get_iothub_auth_headers, validate_iothub_id
from routers.module_config.routes.get_module_twin_config import (
    blob_name_from_desired, get_module_twin_desired, read_module_config_blob,
)
from routers.module_config.secrets import contains_mask, restore_secrets

logger = logging.getLogger("EdgeConfigAPI")

CONTAINER = "iotedge-device-twin"


def content_hash(content: Any) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


async def _upload_and_patch(device: str, module: str, content: Any) -> str:
    config_id = await _upload(device, module, content)
    await _patch_twin(device, module, config_id)
    return config_id


async def _upload(device: str, module: str, content: Any) -> str:
    """Uploads the immutable blob under a new config id and returns the id."""
    if not PUBLIC_STORAGE_ACCOUNT_NAME:
        raise IoTBackendAPIError("Public storage account name is not set. Cannot upload file.", 500)
    if not BLOB_SAS_TOKEN_MODULE_CONF or not BLOB_SAS_TOKEN_MODULE_CONF.strip():
        raise IoTBackendAPIError(
            "BLOB_SAS_TOKEN_MODULE_CONF is not configured. SAS token is required for Edge Device blob access.", 500)

    config_id = uuid.uuid4().hex
    blob_path = f"{device}/{module}/{config_id}"
    try:
        async with BlobContainerContext(
            PUBLIC_STORAGE_ACCOUNT_NAME, CONTAINER, sas_token=BLOB_SAS_TOKEN_MODULE_CONF,
            account_url=MODULE_CONFIG_STORAGE_URL,
        ) as container:
            await container.get_blob_client(blob_path).upload_blob(json.dumps(content), overwrite=True)
    except Exception as exc:
        raise UploadError(f"could not upload data object to blob storage: {exc}", 400)
    return config_id


async def _patch_twin(device: str, module: str, config_id: str) -> None:
    """Points the module twin's desired properties at the blob of `config_id`."""
    blob_path = f"{device}/{module}/{config_id}"
    config_blob_url = blob_url(
        PUBLIC_STORAGE_ACCOUNT_NAME, CONTAINER, blob_path, BLOB_SAS_TOKEN_MODULE_CONF,
        account_url=MODULE_CONFIG_DEVICE_STORAGE_URL or MODULE_CONFIG_STORAGE_URL,
    )
    patch = {"properties": {"desired": {"configBlobUrl": config_blob_url, "configId": config_id}}}
    url = (f"https://{IOT_HUB_NAME}/twins/{encode_iothub_id(device)}/modules/{encode_iothub_id(module)}"
           f"?api-version=2020-05-31-preview")
    responses: Dict[str, Any] = {}
    await patch_async(url, responses, _json=patch, headers=get_iothub_auth_headers())
    response = responses[url]
    if response.status_code >= 400:
        raise IoTBackendAPIError(
            f"could not update module twin of {module} on {device} on the Azure IoT Hub. "
            f"Config-Id: [{config_id}]. Error: {response.text}", response.status_code)


async def _with_repo(repo: Optional[ModuleConfigRepository], call):
    if repo is not None:
        return await call(repo)
    from db import session as db_session
    from db.registry import repo_registry
    async with db_session.AsyncSessionLocal() as session:
        return await call(repo_registry.get(ModuleConfigRepository)(session))


async def _record(repo: Optional[ModuleConfigRepository], fields: Dict[str, Any]) -> Optional[Dict]:
    """Records a revision. Failing to record never undoes a successful device write: the
    revision is then picked up as `external` by the next `sync_external`."""
    try:
        return await _with_repo(repo, lambda r: r.add_revision(**fields))
    except Exception as exc:
        logger.error(f"Could not record module config revision for {fields['device_id']}/{fields['module_name']}: {exc}")
        return None


async def _record_before_patch(repo: Optional[ModuleConfigRepository], fields: Dict[str, Any]) -> Dict:
    """Records the revision of a configuration with secret paths before the twin points at it, so it is
    never read without its secret paths (as an unrecorded or `external` revision). Failing to record
    fails the write."""
    try:
        return await _with_repo(repo, lambda r: r.add_revision(**fields))
    except Exception as exc:
        logger.error(f"Could not record module config revision for {fields['device_id']}/{fields['module_name']}: {exc}")
        raise IoTBackendAPIError("Could not record the module configuration revision; nothing was written", 500)


async def _forget(repo: Optional[ModuleConfigRepository], revision: Dict) -> None:
    """Best effort: removes a revision whose twin patch failed."""
    try:
        await _with_repo(repo, lambda r: r.delete_revision(revision["device_id"], revision["module_name"], revision["id"]))
    except Exception as exc:
        logger.error(f"Could not remove module config revision {revision['id']} after a failed twin update: {exc}")


async def write_module_config(
    device: str,
    module: str,
    content: Any,
    *,
    source: str,
    actor: Optional[str] = None,
    repo: Optional[ModuleConfigRepository] = None,
    template_id: Optional[int] = None,
    values: Optional[Dict[str, Any]] = None,
    comment: Optional[str] = None,
    reapplied_from_id: Optional[int] = None,
    secret_paths: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Writes `content` to the device module and records the revision. Returns
    `{"configId", "revision"}`; `revision` is None if it could not be recorded."""
    validate_iothub_id(device)
    validate_iothub_id(module)
    if secret_paths:
        config_id = await _upload(device, module, content)
    else:
        config_id = await _upload_and_patch(device, module, content)
    fields = {
        "device_id": device,
        "module_name": module,
        "config_id": config_id,
        "blob_name": config_id,
        "source": source,
        "template_id": template_id,
        "values": values,
        "content_hash": content_hash(content),
        "actor": actor,
        "comment": comment,
        "reapplied_from_id": reapplied_from_id,
        "secret_paths": secret_paths or [],
    }
    if not secret_paths:
        return {"configId": config_id, "revision": await _record(repo, fields)}
    revision = await _record_before_patch(repo, fields)
    try:
        await _patch_twin(device, module, config_id)
    except Exception:
        await _forget(repo, revision)
        raise
    return {"configId": config_id, "revision": revision}


async def write_supplied_config(
    device: str,
    module: str,
    content: Any,
    *,
    source: str,
    actor: Optional[str] = None,
    repo: Optional[ModuleConfigRepository] = None,
    files_repo: Optional[FieldFileRepository] = None,
) -> Dict[str, Any]:
    """`write_module_config` for a configuration supplied by a caller, usually one read back from here:
    a mask of the current revision becomes the content of that file as it is now and stays a secret path.
    Raises SecretRestoreError for any other mask or a file that is gone. Content without a mask is
    written as it is."""
    validate_iothub_id(device)
    validate_iothub_id(module)
    secret_paths: List[Dict[str, str]] = []
    if contains_mask(content):
        if repo is not None and files_repo is not None:
            content, secret_paths = await _restore_masks(device, module, content, repo, files_repo)
        else:
            from db import session as db_session
            from db.registry import repo_registry
            async with db_session.AsyncSessionLocal() as session:
                content, secret_paths = await _restore_masks(
                    device, module, content, repo_registry.get(ModuleConfigRepository)(session),
                    repo_registry.get(FieldFileRepository)(session))
    return await write_module_config(device, module, content, source=source, actor=actor, repo=repo,
                                     secret_paths=secret_paths)


async def _restore_masks(device: str, module: str, content: Any, repo: ModuleConfigRepository,
                         files_repo: FieldFileRepository):
    desired = await get_module_twin_desired(device, module)
    config_id = desired.get("configId")
    revision = await repo.get_revision_by_config_id(device, module, str(config_id)) if config_id else None
    previous = (revision or {}).get("secret_paths") or []
    return await restore_secrets(content, previous, files_repo, by="mask")


async def read_revision_content(device: str, module: str, blob_name: str) -> Optional[Any]:
    validate_iothub_id(device)
    validate_iothub_id(module)
    return await read_module_config_blob(device, module, blob_name, missing_ok=True)


async def sync_external(device: str, module: str, repo: ModuleConfigRepository) -> Optional[str]:
    """Records the module's current desired config as an `external` revision if it was not
    written through this service. Returns the current desired `configId`."""
    desired = await get_module_twin_desired(device, module)
    config_id = desired.get("configId")
    if not config_id or not desired.get("configBlobUrl"):
        return config_id
    if await repo.get_revision_by_config_id(device, module, config_id):
        return config_id
    blob_name = blob_name_from_desired(device, module, desired)
    try:
        content = await read_module_config_blob(device, module, blob_name, missing_ok=True)
    except IoTBackendAPIError:
        content = None
    await repo.add_revision(
        device_id=device, module_name=module, config_id=str(config_id), blob_name=blob_name, source="external",
        content_hash=content_hash(content) if content is not None else None,
        comment="Written outside the platform's module configuration service",
    )
    return config_id
