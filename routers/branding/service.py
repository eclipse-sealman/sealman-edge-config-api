import hashlib
import re
import time
from typing import Dict, Optional, Tuple

from db.repos.branding import BrandingAsset, BrandingSettings
from .schemas import BrandingResponse

ASSET_KINDS = ("logo", "favicon")
ASSET_MAX_BYTES = {"logo": 512 * 1024, "favicon": 128 * 1024}
ASSET_ALLOWED_TYPES = {
    "logo": {"image/png", "image/jpeg", "image/webp", "image/svg+xml"},
    "favicon": {"image/x-icon", "image/png", "image/svg+xml"},
}

# SVGs may carry scripts: a sandboxed CSP keeps them inert even when opened directly.
ASSET_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}

_SVG_START = re.compile(rb"^\s*(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*(?:<!DOCTYPE[^>]*>\s*)?<svg[\s>]", re.DOTALL | re.IGNORECASE)


def sniff_image_type(content: bytes) -> Optional[str]:
    """Detects the image type from the file bytes; the client-declared type is never trusted."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if _SVG_START.match(content.removeprefix(b"\xef\xbb\xbf")[:4096]):
        return "image/svg+xml"
    return None


def compute_etag(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:20]


def to_response(settings: BrandingSettings) -> BrandingResponse:
    return BrandingResponse(
        theme_id=settings.theme_id,
        variables=settings.variables,
        app_title=settings.app_title,
        logo_version=settings.asset_etags.get("logo"),
        favicon_version=settings.asset_etags.get("favicon"),
    )


class BrandingCache:
    """
    Short-lived in-process cache for the public endpoints, so unauthenticated page loads hit the
    database at most once per TTL and replica. Writes on this replica invalidate it immediately;
    other replicas pick up changes after the TTL.
    """

    TTL_SECONDS = 30.0

    def __init__(self):
        self._settings: Optional[Tuple[float, BrandingResponse]] = None
        self._assets: Dict[str, Tuple[float, Optional[BrandingAsset]]] = {}

    def _fresh(self, stored_at: float) -> bool:
        return time.monotonic() - stored_at < self.TTL_SECONDS

    def get_settings(self) -> Optional[BrandingResponse]:
        if self._settings and self._fresh(self._settings[0]):
            return self._settings[1]
        return None

    def put_settings(self, settings: BrandingResponse) -> None:
        self._settings = (time.monotonic(), settings)

    def get_asset(self, kind: str) -> Tuple[bool, Optional[BrandingAsset]]:
        """(hit, asset) - a hit may be None, meaning "no custom asset uploaded"."""
        entry = self._assets.get(kind)
        if entry and self._fresh(entry[0]):
            return True, entry[1]
        return False, None

    def put_asset(self, kind: str, asset: Optional[BrandingAsset]) -> None:
        self._assets[kind] = (time.monotonic(), asset)

    def clear(self) -> None:
        self._settings = None
        self._assets.clear()


branding_cache = BrandingCache()
