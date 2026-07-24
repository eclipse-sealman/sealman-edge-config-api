from typing import List

from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from routers.network_discovery.mapping_helpers import role_field_key, resolved_value


async def get_network_scan_ports(
    device: str,
    endpoint_repo: EndpointRepository,
    service_repo: ServiceRepository,
) -> List[int]:
    service_types = await service_repo.get_service_types()

    ports = set()
    for service_type in service_types:
        port_field = role_field_key(service_type["fields"], service_type["mapping"], "port")
        if port_field is None:
            continue
        default_port = (service_type["fields"].get(port_field) or {}).get("default")
        if default_port is None:
            continue
        try:
            ports.add(int(default_port))
        except (TypeError, ValueError):
            continue

    service_types_by_id = {st["type_id"]: st for st in service_types}
    endpoints = await endpoint_repo.get_endpoints(device_id=device)
    for endpoint in endpoints:
        services = await service_repo.get_services(endpoint_id=endpoint["endpoint_id"])
        for service in services:
            service_type = service_types_by_id.get(service["type_id"])
            if service_type is None:
                continue
            port_field = role_field_key(service_type["fields"], service_type["mapping"], "port")
            port_value = resolved_value(service["service_data"], port_field)
            if port_value is None:
                continue
            try:
                ports.add(int(port_value))
            except (TypeError, ValueError):
                continue

    return sorted(ports)
