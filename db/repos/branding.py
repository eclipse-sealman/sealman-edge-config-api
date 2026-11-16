from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class BrandingSettings:
    theme_id: str = "default"
    variables: Dict[str, str] = field(default_factory=dict)
    app_title: str = ""
    # kind -> etag of the uploaded asset; missing kind means the bundled default is used.
    asset_etags: Dict[str, str] = field(default_factory=dict)


@dataclass
class BrandingAsset:
    content: bytes
    content_type: str
    etag: str


class BrandingRepository(ABC):
    @abstractmethod
    async def get_branding(self) -> BrandingSettings:
        """Returns the branding, or the defaults if none was saved yet. Never loads image bytes."""
        ...

    @abstractmethod
    async def save_branding(self, theme_id: str, variables: Dict[str, str], app_title: str) -> BrandingSettings:
        ...

    @abstractmethod
    async def get_asset(self, kind: str) -> Optional[BrandingAsset]:
        ...

    @abstractmethod
    async def set_asset(self, kind: str, content: bytes, content_type: str, etag: str) -> None:
        ...

    @abstractmethod
    async def delete_asset(self, kind: str) -> None:
        """No-op if the asset doesn't exist."""
        ...
