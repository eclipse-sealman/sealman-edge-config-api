from typing import List

from db.repos.extra_network_range import ExtraNetworkRangeRepository
from routers.network_discovery.schemas import NetworkRange


async def get_network_scan_ranges(device: str, repo: ExtraNetworkRangeRepository) -> List[NetworkRange]:
    ranges = await repo.list_ranges(device)
    return [NetworkRange(networkDefinition=network_definition, subnetMask=subnet_mask) for network_definition, subnet_mask in ranges]


async def put_network_scan_ranges(
    device: str, ranges: List[NetworkRange], repo: ExtraNetworkRangeRepository
) -> List[NetworkRange]:
    await repo.replace_ranges(device, [(r.networkDefinition, r.subnetMask) for r in ranges])
    return ranges
