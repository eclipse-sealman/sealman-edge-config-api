from typing import List

from db.repos.scan_ports import ScanPortsRepository
from routers.network_discovery.schemas import DeviceScanPortsAdd


async def post_device_scan_ports(
    device: str,
    body: DeviceScanPortsAdd,
    scan_ports_repo: ScanPortsRepository,
) -> List[int]:
    """Persists extra ports for this device (Overview page's "Scan Network" dialog), so they're
    included in every automatic scan for it going forward, not just the one-time scan that first
    requested them. Returns every extra port persisted for this device so far."""
    await scan_ports_repo.add_device_ports(device, body.ports)
    return await scan_ports_repo.get_device_ports(device)
