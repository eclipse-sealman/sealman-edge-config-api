from fastapi import Depends, HTTPException, status

from db.repos.platform_functions import PlatformFunctionsRepository
from db.session import get_repository

NETWORK_DISCOVER_MODULE = "seal-app-net-discover"
NETWORK_SCAN_DISABLED_DETAIL = "Network scan is disabled on this platform"


async def is_network_scan_enabled(
    repo: PlatformFunctionsRepository = Depends(get_repository(PlatformFunctionsRepository)),
) -> bool:
    return (await repo.get_functions()).network_scan_enabled


async def require_network_scan_enabled(enabled: bool = Depends(is_network_scan_enabled)) -> None:
    if not enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, NETWORK_SCAN_DISABLED_DETAIL)
