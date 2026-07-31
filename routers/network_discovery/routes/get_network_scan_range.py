from ipaddress import ip_address, ip_network
from typing import List, Optional, Tuple

from db.repos.endpoint import EndpointRepository
from db.repos.network_range import NetworkRangeRepository
from routers.network_discovery.mapping_helpers import role_field_key, resolved_value
from routers.network_discovery.schemas import NetworkRange

# Bounds so the computed range stays useful and safe: never narrower than /24 (so a handful of
# known IPs still scans a real neighborhood, turning up nearby unidentified devices too), and
# never wider than /21 (2048 addresses) so a stray/mistyped default IP far from the rest can't
# blow the scan up to an impractical size. This /21 ceiling is a hard cap even against the
# "never shrink" rule below - an extreme new outlier can still fail to widen the persisted range
# rather than let it grow unbounded.
WIDEST_SUBNET_MASK = 21
NARROWEST_SUBNET_MASK = 24


def _network_bounds(network_definition: str, subnet_mask: int) -> Tuple[int, int]:
    network = ip_network(f"{network_definition}/{subnet_mask}", strict=False)
    return int(network.network_address), int(network.broadcast_address)


def _covering_network(points: List[int]) -> Tuple[str, int]:
    lo, hi = min(points), max(points)
    diff = lo ^ hi
    natural_prefixlen = 32 - diff.bit_length()
    subnet_mask = max(WIDEST_SUBNET_MASK, min(natural_prefixlen, NARROWEST_SUBNET_MASK))
    network = ip_network((lo, subnet_mask), strict=False)
    return str(network.network_address), network.prefixlen


async def get_network_scan_range(
    device: str,
    endpoint_repo: EndpointRepository,
    network_range_repo: NetworkRangeRepository,
) -> Optional[NetworkRange]:
    endpoint_types = {et["type_id"]: et for et in await endpoint_repo.get_endpoint_types()}
    known_ips: List[str] = []

    # Only IPs actually configured on THIS device's own endpoints count as evidence for its scan
    # range. Endpoint types' `default` IP values are global/system-wide (not scoped to any
    # device), so they must never widen or seed a specific device's persisted range - otherwise a
    # placeholder default typed into any type, anywhere, permanently pollutes every device.
    for endpoint in await endpoint_repo.get_endpoints(device_id=device):
        endpoint_type = endpoint_types.get(endpoint["type_id"])
        if endpoint_type is None:
            continue
        ip_field = role_field_key(endpoint_type["fields"], endpoint_type["mapping"], "ip")
        ip_value = resolved_value(endpoint["endpoint_data"], ip_field)
        if ip_value:
            known_ips.append(str(ip_value))

    points: List[int] = []
    for ip in known_ips:
        try:
            points.append(int(ip_address(ip)))
        except ValueError:
            continue

    stored: Optional[Tuple[str, int]] = await network_range_repo.get_range(device)

    # No default entry: if nothing is known (no configured endpoints) and nothing has ever been
    # derived/stored before, there's simply no automatic baseline range - the caller falls back
    # to only scanning whatever extra range/ports/IPs the user has added for this one scan (see
    # ScanNetworkDialog's "Read Network Configuration" button).
    if not points and stored is None:
        return None

    if stored is not None:
        # Anchor the computation with the previously persisted range's own bounds, so the result
        # can only widen to also cover newly known IPs - it never shrinks just because an IP that
        # used to justify a wider range was edited or deleted.
        points.extend(_network_bounds(stored[0], stored[1]))

    if not points:
        return None

    network_definition, subnet_mask = _covering_network(points)

    if stored is None or (network_definition, subnet_mask) != stored:
        await network_range_repo.set_range(device, network_definition, subnet_mask)

    return NetworkRange(networkDefinition=network_definition, subnetMask=subnet_mask)
