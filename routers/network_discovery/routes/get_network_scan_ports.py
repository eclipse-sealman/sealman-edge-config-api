from typing import List

from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from db.repos.scan_ports import ScanPortsRepository
from routers.network_discovery.mapping_helpers import role_field_key, resolved_value


async def get_network_scan_ports(
    device: str,
    endpoint_repo: EndpointRepository,
    service_repo: ServiceRepository,
    scan_ports_repo: ScanPortsRepository,
) -> List[int]:
    # The global baseline (every service type's default port, at minimum - see
    # routers/service/router.py:create_service_type) plus whatever extra ports have been added
    # specifically for this device (Overview page's "Scan Network" dialog) - both persisted
    # tables, editable independently of any single service type/device from now on.
    ports = set(await scan_ports_repo.get_default_ports())
    ports.update(await scan_ports_repo.get_device_ports(device))

    service_types_by_id = {st["type_id"]: st for st in await service_repo.get_service_types()}
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
