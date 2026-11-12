from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from db.repos.branding import BrandingRepository
from db.session import get_repository

from .router import AssetKind
from .schemas import BrandingResponse
from .service import ASSET_SECURITY_HEADERS, branding_cache, to_response

# Mounted as its own app so the main app's global auth dependency does not apply: the branding
# is needed before sign-in (tab title / icon) and contains nothing confidential.
public_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@public_app.get("/branding", response_model=BrandingResponse)
async def get_public_branding(
    response: Response,
    repo: BrandingRepository = Depends(get_repository(BrandingRepository)),
):
    settings = branding_cache.get_settings()
    if settings is None:
        settings = to_response(await repo.get_branding())
        branding_cache.put_settings(settings)
    response.headers["Cache-Control"] = "no-cache"
    return settings


@public_app.get("/branding/{kind}")
async def get_public_branding_asset(
    kind: AssetKind,
    request: Request,
    v: Optional[str] = None,
    repo: BrandingRepository = Depends(get_repository(BrandingRepository)),
):
    hit, asset = branding_cache.get_asset(kind)
    if not hit:
        asset = await repo.get_asset(kind)
        branding_cache.put_asset(kind, asset)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no custom {kind} uploaded")

    # Versioned URLs (?v=<etag>) never change content, so browsers may cache them for good.
    headers = {
        **ASSET_SECURITY_HEADERS,
        "ETag": f'"{asset.etag}"',
        "Cache-Control": "public, max-age=31536000, immutable" if v == asset.etag else "no-cache",
    }
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=asset.content, media_type=asset.content_type, headers=headers)
