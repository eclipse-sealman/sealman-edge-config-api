import re
import unicodedata
from typing import Any, Dict
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from authorization.abac_permission_check import ABACPermissionCheck, ABACResolvedDeviceCheck
from authorization.permission_types import Device
from db.repos.device import DeviceRepository
from db.repos.endpoint import EndpointRepository
from db.repos.field_file import FieldFileRepository
from db.repos.service import ServiceRepository
from db.session import get_repository
from file_crypto import FileEncryptionError, decrypt, encrypt, encryption_available
from file_parsing import parse_upload
from routers.branding.router import _read_body_limited

# Files of file fields on endpoints, services and devices. The owner's data holds only the reference.
field_files = APIRouter(tags=["Files"])

MAX_FILE_BYTES = 64 * 1024
NOT_CONFIGURED = "File storage is not configured (FILE_ENCRYPTION_KEY)"

_OCTET_STREAM = {
    "requestBody": {
        "required": True,
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}

_endpoint_write = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_WRITE, "endpoint", "endpoint_id"))
_endpoint_read = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_READ, "endpoint", "endpoint_id"))
_service_write = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_WRITE, "service", "service_id"))
_service_read = Depends(ABACResolvedDeviceCheck(Device.ENDPOINT_READ, "service", "service_id"))
_device_write = Depends(ABACPermissionCheck(Device.METADATA_WRITE, device_path="device_id"))
_device_read = Depends(ABACPermissionCheck(Device.READ, device_path="device_id"))


async def _upload(
    owner_kind: str, owner_id: str, field: str, name: str, request: Request,
    fields: Dict[str, Any], auth: dict, repo: FieldFileRepository,
) -> Dict[str, Any]:
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in name):
        raise HTTPException(422, "The file name contains characters that are not allowed")
    definition = fields.get(field)
    if not isinstance(definition, dict) or definition.get("type") != "file":
        raise HTTPException(422, f"'{field}' is not a file field")
    raw = await _read_body_limited(request, MAX_FILE_BYTES)
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is empty")
    if not encryption_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)
    try:
        parsed = parse_upload(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    secret = bool(definition.get("secret"))
    if parsed.kind == "private-key" and not secret:
        raise HTTPException(
            422, f"'{field}' holds a private key: mark the field as secret"
        )
    try:
        content = encrypt(parsed.pem)
    except FileEncryptionError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)
    stored = await repo.put(
        owner_kind, owner_id, field,
        name=name, content=content, secret=secret, sha256=parsed.sha256, kind=parsed.kind,
        subject=parsed.subject, not_after=parsed.not_after, uploaded_by=auth["user_name"],
    )
    return stored.reference()


async def _download(owner_kind: str, owner_id: str, field: str, repo: FieldFileRepository) -> Response:
    stored = await repo.get(owner_kind, owner_id, field)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No file")
    if stored.secret:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This file is secret and cannot be downloaded")
    try:
        pem = decrypt(stored.content)
    except FileEncryptionError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", stored.name)
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={
            "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(stored.name)}",
            "Cache-Control": "no-store",
        },
    )


async def _remove(owner_kind: str, owner_id: str, field: str, repo: FieldFileRepository) -> Response:
    if not await repo.remove(owner_kind, owner_id, field):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No file")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _endpoint_fields(endpoint_id: str, endpoints: EndpointRepository) -> Dict[str, Any]:
    endpoint = await endpoints.get_endpoint(endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Endpoint '{endpoint_id}' not found")
    return (await endpoints.get_endpoint_type(endpoint["type_id"]))["fields"]


async def _service_fields(service_id: str, services: ServiceRepository) -> Dict[str, Any]:
    service = await services.get_service(service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Service '{service_id}' not found")
    return (await services.get_service_type(service["type_id"]))["fields"]


async def _device_fields(device_id: str, devices: DeviceRepository) -> Dict[str, Any]:
    fields = await devices.get_effective_fields(device_id)
    if fields is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Device '{device_id}' not found")
    return fields


@field_files.put("/endpoints/{endpoint_id}/files/{field}", openapi_extra=_OCTET_STREAM, summary="Upload an endpoint file")
async def upload_endpoint_file(
    endpoint_id: str, field: str, request: Request,
    name: str = Query(min_length=1, max_length=255, description="File name"),
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    endpoints: EndpointRepository = Depends(get_repository(EndpointRepository)),
    auth=_endpoint_write,
):
    fields = await _endpoint_fields(endpoint_id, endpoints)
    return await _upload("endpoint", endpoint_id, field, name, request, fields, auth, repo)


@field_files.get("/endpoints/{endpoint_id}/files/{field}", summary="Download an endpoint file")
async def download_endpoint_file(
    endpoint_id: str, field: str,
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _auth=_endpoint_read,
):
    return await _download("endpoint", endpoint_id, field, repo)


@field_files.delete("/endpoints/{endpoint_id}/files/{field}", status_code=204, summary="Remove an endpoint file")
async def remove_endpoint_file(
    endpoint_id: str, field: str,
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _auth=_endpoint_write,
):
    return await _remove("endpoint", endpoint_id, field, repo)


@field_files.put("/services/{service_id}/files/{field}", openapi_extra=_OCTET_STREAM, summary="Upload a service file")
async def upload_service_file(
    service_id: str, field: str, request: Request,
    name: str = Query(min_length=1, max_length=255, description="File name"),
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    services: ServiceRepository = Depends(get_repository(ServiceRepository)),
    auth=_service_write,
):
    fields = await _service_fields(service_id, services)
    return await _upload("service", service_id, field, name, request, fields, auth, repo)


@field_files.get("/services/{service_id}/files/{field}", summary="Download a service file")
async def download_service_file(
    service_id: str, field: str,
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _auth=_service_read,
):
    return await _download("service", service_id, field, repo)


@field_files.delete("/services/{service_id}/files/{field}", status_code=204, summary="Remove a service file")
async def remove_service_file(
    service_id: str, field: str,
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _auth=_service_write,
):
    return await _remove("service", service_id, field, repo)


@field_files.put("/devices/{device_id}/files/{field}", openapi_extra=_OCTET_STREAM, summary="Upload a device file")
async def upload_device_file(
    device_id: str, field: str, request: Request,
    name: str = Query(min_length=1, max_length=255, description="File name"),
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    devices: DeviceRepository = Depends(get_repository(DeviceRepository)),
    auth=_device_write,
):
    fields = await _device_fields(device_id, devices)
    return await _upload("device", device_id, field, name, request, fields, auth, repo)


@field_files.get("/devices/{device_id}/files/{field}", summary="Download a device file")
async def download_device_file(
    device_id: str, field: str,
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _auth=_device_read,
):
    return await _download("device", device_id, field, repo)


@field_files.delete("/devices/{device_id}/files/{field}", status_code=204, summary="Remove a device file")
async def remove_device_file(
    device_id: str, field: str,
    repo: FieldFileRepository = Depends(get_repository(FieldFileRepository)),
    _auth=_device_write,
):
    return await _remove("device", device_id, field, repo)
