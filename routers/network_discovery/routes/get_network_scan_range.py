from ipaddress import ip_address, ip_network
from typing import List, Tuple

from db.repos.endpoint import EndpointRepository
from routers.network_discovery.mapping_helpers import role_field_key, resolved_value
from routers.network_discovery.schemas import NetworkRange

# Bounds so the computed range stays useful and safe: never narrower than /24 (so a handful of
# known IPs still scans a real neighborhood, turning up nearby unidentified devices too), and
# never wider than /21 (2048 addresses) so a stray/mistyped default IP far from the rest can't
# blow the scan up to an impractical size.
WIDEST_SUBNET_MASK = 21
NARROWEST_SUBNET_MASK = 24

FALLBACK_NETWORK_DEFINITION = "172.22.220.0"
FALLBACK_SUBNET_MASK = 23


def _smallest_covering_network(ip_strings: List[str]) -> Tuple[str, int]:
    addrs = sorted(int(ip_address(ip)) for ip in ip_strings)
    lo, hi = addrs[0], addrs[-1]
    diff = lo ^ hi
    natural_prefixlen = 32 - diff.bit_length()
    subnet_mask = max(WIDEST_SUBNET_MASK, min(natural_prefixlen, NARROWEST_SUBNET_MASK))
    network = ip_network((lo, subnet_mask), strict=False)
    return str(network.network_address), network.prefixlen


async def get_network_scan_range(device: str, endpoint_repo: EndpointRepository) -> NetworkRange:
    endpoint_types = {et["type_id"]: et for et in await endpoint_repo.get_endpoint_types()}
    known_ips: List[str] = []

    for endpoint in await endpoint_repo.get_endpoints(device_id=device):
        endpoint_type = endpoint_types.get(endpoint["type_id"])
        if endpoint_type is None:
            continue
        ip_field = role_field_key(endpoint_type["fields"], endpoint_type["mapping"], "ip")
        ip_value = resolved_value(endpoint["endpoint_data"], ip_field)
        if ip_value:
            known_ips.append(str(ip_value))

    for endpoint_type in endpoint_types.values():
        ip_field = role_field_key(endpoint_type["fields"], endpoint_type["mapping"], "ip")
        if ip_field is None:
            continue
        default_ip = (endpoint_type["fields"].get(ip_field) or {}).get("default")
        if default_ip:
            known_ips.append(str(default_ip))

    valid_ips: List[str] = []
    for ip in known_ips:
        try:
            ip_address(ip)
        except ValueError:
            continue
        valid_ips.append(ip)

    if not valid_ips:
        return NetworkRange(networkDefinition=FALLBACK_NETWORK_DEFINITION, subnetMask=FALLBACK_SUBNET_MASK)

    network_definition, subnet_mask = _smallest_covering_network(valid_ips)
    return NetworkRange(networkDefinition=network_definition, subnetMask=subnet_mask)
