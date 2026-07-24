from typing import Any, Dict, List

from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from routers.network_discovery.mapping_helpers import role_field_key, resolved_value
from routers.network_discovery.schemas import (
    MappedEndpoint,
    MappedPort,
    NetworkOverview,
    NetworkScan,
)


async def build_network_overview(
    device: str,
    scan: NetworkScan,
    endpoint_repo: EndpointRepository,
    service_repo: ServiceRepository,
) -> NetworkOverview:
    endpoint_types = {et["type_id"]: et for et in await endpoint_repo.get_endpoint_types()}
    service_types = {st["type_id"]: st for st in await service_repo.get_service_types()}
    configured_endpoints = await endpoint_repo.get_endpoints(device_id=device)

    configured_endpoints_by_ip: Dict[str, Dict[str, Any]] = {}
    for endpoint in configured_endpoints:
        endpoint_type = endpoint_types.get(endpoint["type_id"])
        if endpoint_type is None:
            continue
        ip_field = role_field_key(endpoint_type["fields"], endpoint_type["mapping"], "ip")
        ip_value = resolved_value(endpoint["endpoint_data"], ip_field)
        if ip_value:
            configured_endpoints_by_ip[str(ip_value)] = endpoint

    default_endpoint_types_by_ip: Dict[str, Dict[str, Any]] = {}
    for endpoint_type in endpoint_types.values():
        ip_field = role_field_key(endpoint_type["fields"], endpoint_type["mapping"], "ip")
        if ip_field is None:
            continue
        default_ip = (endpoint_type["fields"].get(ip_field) or {}).get("default")
        if default_ip:
            default_endpoint_types_by_ip.setdefault(str(default_ip), endpoint_type)

    mapped_endpoints: List[MappedEndpoint] = []
    for host in scan.scanResults:
        ip_str = str(host.ip)
        configured_endpoint = configured_endpoints_by_ip.get(ip_str)
        default_endpoint_type = None if configured_endpoint else default_endpoint_types_by_ip.get(ip_str)

        configured_services: List[Dict[str, Any]] = []
        if configured_endpoint is not None:
            source = "configured"
            endpoint_id = configured_endpoint["endpoint_id"]
            type_id = configured_endpoint["type_id"]
            type_label = configured_endpoint["type_label"]
            type_description = configured_endpoint["type_description"]
            endpoint_data = configured_endpoint["endpoint_data"]
            configured_services = await service_repo.get_services(endpoint_id=endpoint_id)
        elif default_endpoint_type is not None:
            source = "default"
            endpoint_id = None
            type_id = default_endpoint_type["type_id"]
            type_label = default_endpoint_type["label"]
            type_description = default_endpoint_type["description"]
            endpoint_data = None
        else:
            source = "unidentified"
            endpoint_id = None
            type_id = None
            type_label = None
            type_description = None
            endpoint_data = None

        configured_services_by_port: Dict[int, Dict[str, Any]] = {}
        for service in configured_services:
            service_type = service_types.get(service["type_id"])
            if service_type is None:
                continue
            port_field = role_field_key(service_type["fields"], service_type["mapping"], "port")
            port_value = resolved_value(service["service_data"], port_field)
            if port_value is None:
                continue
            try:
                configured_services_by_port[int(port_value)] = service
            except (TypeError, ValueError):
                continue

        default_service_types_by_port: Dict[int, Dict[str, Any]] = {}
        if source in ("configured", "default"):
            for service_type in service_types.values():
                port_field = role_field_key(service_type["fields"], service_type["mapping"], "port")
                if port_field is None:
                    continue
                default_port = (service_type["fields"].get(port_field) or {}).get("default")
                if default_port is None:
                    continue
                try:
                    default_service_types_by_port.setdefault(int(default_port), service_type)
                except (TypeError, ValueError):
                    continue

        mapped_ports: List[MappedPort] = []
        for port_str, port_status in host.ports.items():
            try:
                port_num = int(port_str)
            except ValueError:
                continue

            configured_service = configured_services_by_port.get(port_num)
            default_service_type = None if configured_service else default_service_types_by_port.get(port_num)

            if configured_service is not None:
                mapped_ports.append(MappedPort(
                    port=port_num,
                    status=port_status.status,
                    lastStatusChange=port_status.lastStatusChange,
                    source="configured",
                    service_id=configured_service["service_id"],
                    type_id=configured_service["type_id"],
                    type_label=configured_service["type_label"],
                    type_description=configured_service["type_description"],
                ))
            elif default_service_type is not None:
                mapped_ports.append(MappedPort(
                    port=port_num,
                    status=port_status.status,
                    lastStatusChange=port_status.lastStatusChange,
                    source="default",
                    type_id=default_service_type["type_id"],
                    type_label=default_service_type["label"],
                    type_description=default_service_type["description"],
                ))
            else:
                mapped_ports.append(MappedPort(
                    port=port_num,
                    status=port_status.status,
                    lastStatusChange=port_status.lastStatusChange,
                    source="unidentified",
                ))

        mapped_endpoints.append(MappedEndpoint(
            ip=ip_str,
            status=host.status,
            lastStatusChange=host.lastStatusChange,
            source=source,
            endpoint_id=endpoint_id,
            type_id=type_id,
            type_label=type_label,
            type_description=type_description,
            endpoint_data=endpoint_data,
            ports=mapped_ports,
        ))

    return NetworkOverview(scanDefinition=scan.scanDefinition, endpoints=mapped_endpoints)
