from typing import Literal

from fastapi import Depends, HTTPException, Request, status

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Platform
from db.repos.branding import BrandingRepository
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter

from .schemas import BrandingResponse, BrandingUpdateRequest
from .service import (
    ASSET_ALLOWED_TYPES,
    ASSET_MAX_BYTES,
    branding_cache,
    compute_etag,
    sniff_image_type,
    to_response,
)

# Reads are public (see public.py); everything here changes the branding for all users.
branding = BaseAPIRouter(prefix="/platform/branding", tags=["Platform Configuration"])

AssetKind = Literal["logo", "favicon"]


async def _read_body_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"file must not exceed {limit // 1024} KB")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"file must not exceed {limit // 1024} KB")
    return bytes(body)


@branding.put("", response_model=BrandingResponse)
async def update_branding(
    request: BrandingUpdateRequest,
    repo: BrandingRepository = Depends(get_repository(BrandingRepository)),
    _=Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    settings = await repo.save_branding(request.theme_id, request.variables, request.app_title)
    branding_cache.clear()
    return to_response(settings)


@branding.put(
    "/{kind}",
    response_model=BrandingResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_branding_asset(
    kind: AssetKind,
    request: Request,
    repo: BrandingRepository = Depends(get_repository(BrandingRepository)),
    _=Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    """Raw image bytes as request body; the type is detected from the content."""
    content = await _read_body_limited(request, ASSET_MAX_BYTES[kind])
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is empty")
    content_type = sniff_image_type(content)
    if content_type not in ASSET_ALLOWED_TYPES[kind]:
        allowed = ", ".join(sorted(t.removeprefix("image/") for t in ASSET_ALLOWED_TYPES[kind]))
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"unsupported image type, allowed: {allowed}")
    await repo.set_asset(kind, content, content_type, compute_etag(content))
    branding_cache.clear()
    return to_response(await repo.get_branding())


@branding.delete("/{kind}", response_model=BrandingResponse)
async def delete_branding_asset(
    kind: AssetKind,
    repo: BrandingRepository = Depends(get_repository(BrandingRepository)),
    _=Depends(ABACPermissionCheck(Platform.CONFIG_WRITE, device_path=None)),
):
    await repo.delete_asset(kind)
    branding_cache.clear()
    return to_response(await repo.get_branding())
