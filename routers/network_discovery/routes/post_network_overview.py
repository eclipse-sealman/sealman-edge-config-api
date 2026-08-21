import ipaddress
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from db.repos.endpoint import EndpointRepository
from db.repos.host_status import HostStatusRepository
from db.repos.service import ServiceRepository
from routers.network_discovery.mapping_helpers import role_field_key, resolved_value
from routers.network_discovery.schemas import (
    MappedEndpoint,
    MappedPort,
    NetworkDiscover,
    NetworkOverview,
    NetworkScan,
)

logger = logging.getLogger(__name__)


def _resolve_status_change(
    previous: Optional[Tuple[str, datetime]], status: str, now: datetime
) -> datetime:
    """
    A fresh scan runs every few seconds, so the module's own `lastStatusChange` can't be trusted
    to mean anything but "now". Instead, only advance the timestamp when the status actually
    differs from the last one we persisted for this host/port - otherwise keep the old timestamp.
    """
    if previous is None or previous[0] != status:
        return now
    return previous[1]


def _build_instance_data(
    fields: Dict[str, Any], locked_field_key: Optional[str], locked_value: Any
) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for field_key, field_def in fields.items():
        default = field_def.get("default")
        if default is not None:
            data[field_key] = default
    if locked_field_key is not None:
        data[locked_field_key] = locked_value
    return data


def _has_missing_required(fields: Dict[str, Any], data: Dict[str, Any]) -> bool:
    return any(field_def.get("required") and data.get(field_key) is None for field_key, field_def in fields.items())


async def _build_last_known_endpoint(
    ip_str: str,
    configured_endpoint: Dict[str, Any],
    service_types: Dict[str, Dict[str, Any]],
    service_repo: ServiceRepository,
    previous_host_statuses: Dict[str, Tuple[str, datetime]],
    previous_port_statuses: Dict[Tuple[str, int], Tuple[str, datetime]],
    now: datetime,
) -> MappedEndpoint:
    """
    Maps a configured endpoint to its last persisted status/timestamp (from `device_host_status`/
    `device_port_status`) rather than anything freshly scanned - used whenever a configured
    endpoint wasn't actually probed (this scan cycle didn't cover it, or there was no scan at
    all), so it still shows up instead of silently disappearing.
    """
    endpoint_id = configured_endpoint["endpoint_id"]
    configured_services = await service_repo.get_services(endpoint_id=endpoint_id)

    mapped_ports: List[MappedPort] = []
    for service in configured_services:
        service_type = service_types.get(service["type_id"])
        if service_type is None:
            continue
        port_field = role_field_key(service_type["fields"], service_type["mapping"], "port")
        port_value = resolved_value(service["service_data"], port_field)
        if port_value is None:
            continue
        try:
            port_num = int(port_value)
        except (TypeError, ValueError):
            continue
        previous = previous_port_statuses.get((ip_str, port_num))
        mapped_ports.append(MappedPort(
            port=port_num,
            status=previous[0] if previous else "offline",
            lastStatusChange=(previous[1] if previous else now).isoformat(),
            source="configured",
            service_id=service["service_id"],
            type_id=service["type_id"],
            type_label=service["type_label"],
            type_description=service["type_description"],
            service_data=service["service_data"],
            browser_kind=service_type["browser_kind"],
        ))

    previous_host = previous_host_statuses.get(ip_str)
    return MappedEndpoint(
        ip=ip_str,
        status=previous_host[0] if previous_host else "offline",
        lastStatusChange=(previous_host[1] if previous_host else now).isoformat(),
        source="configured",
        endpoint_id=endpoint_id,
        type_id=configured_endpoint["type_id"],
        type_label=configured_endpoint["type_label"],
        type_description=configured_endpoint["type_description"],
        endpoint_data=configured_endpoint["endpoint_data"],
        ports=mapped_ports,
    )


async def _build_confirmed_offline_endpoint(
    ip_str: str,
    configured_endpoint: Dict[str, Any],
    service_types: Dict[str, Dict[str, Any]],
    service_repo: ServiceRepository,
    previous_host_statuses: Dict[str, Tuple[str, datetime]],
    previous_port_statuses: Dict[Tuple[str, int], Tuple[str, datetime]],
    new_host_statuses: Dict[str, Tuple[str, datetime]],
    new_port_statuses: Dict[Tuple[str, int], Tuple[str, datetime]],
    now: datetime,
) -> MappedEndpoint:
    """
    Maps a configured endpoint whose IP fell within *this* scan's target range/subnet but simply
    wasn't reported back - the scanner only ever reports hosts it actually finds (alive, with at
    least one open port), it never lists misses explicitly. That makes "absent from
    `scan.scanResults`" ambiguous between "wasn't covered by this scan at all" (handled by
    `_build_last_known_endpoint`, which preserves whatever was last persisted) and "was covered,
    but is genuinely gone now". This function handles the latter: since the scan really did cover
    this IP, its absence is confirmed offline, so - unlike `_build_last_known_endpoint` - this
    actually persists "offline" (into `new_host_statuses`/`new_port_statuses`), advancing
    `lastStatusChange` the first time it flips, instead of leaving the previous status in place
    forever.
    """
    endpoint_id = configured_endpoint["endpoint_id"]
    configured_services = await service_repo.get_services(endpoint_id=endpoint_id)

    mapped_ports: List[MappedPort] = []
    for service in configured_services:
        service_type = service_types.get(service["type_id"])
        if service_type is None:
            continue
        port_field = role_field_key(service_type["fields"], service_type["mapping"], "port")
        port_value = resolved_value(service["service_data"], port_field)
        if port_value is None:
            continue
        try:
            port_num = int(port_value)
        except (TypeError, ValueError):
            continue
        port_changed_at = _resolve_status_change(previous_port_statuses.get((ip_str, port_num)), "offline", now)
        new_port_statuses[(ip_str, port_num)] = ("offline", port_changed_at)
        mapped_ports.append(MappedPort(
            port=port_num,
            status="offline",
            lastStatusChange=port_changed_at.isoformat(),
            source="configured",
            service_id=service["service_id"],
            type_id=service["type_id"],
            type_label=service["type_label"],
            type_description=service["type_description"],
            service_data=service["service_data"],
            browser_kind=service_type["browser_kind"],
        ))

    host_changed_at = _resolve_status_change(previous_host_statuses.get(ip_str), "offline", now)
    new_host_statuses[ip_str] = ("offline", host_changed_at)
    return MappedEndpoint(
        ip=ip_str,
        status="offline",
        lastStatusChange=host_changed_at.isoformat(),
        source="configured",
        endpoint_id=endpoint_id,
        type_id=configured_endpoint["type_id"],
        type_label=configured_endpoint["type_label"],
        type_description=configured_endpoint["type_description"],
        endpoint_data=configured_endpoint["endpoint_data"],
        ports=mapped_ports,
    )


async def build_last_known_network_overview(
    device: str,
    endpoint_repo: EndpointRepository,
    service_repo: ServiceRepository,
    host_status_repo: HostStatusRepository,
) -> NetworkOverview:
    """
    A scan-free counterpart to `build_network_overview`: reports every configured endpoint/service
    for this device at its last persisted status/timestamp, with no live probing at all. Used as a
    fallback when a live scan can't run (e.g. the device itself is unreachable via IoT Hub), so
    what's already configured still shows up - as its last known state - instead of the page
    having nothing to show.
    """
    endpoint_types = {et["type_id"]: et for et in await endpoint_repo.get_endpoint_types()}
    service_types = {st["type_id"]: st for st in await service_repo.get_service_types()}
    configured_endpoints = await endpoint_repo.get_endpoints(device_id=device)

    now = datetime.now(timezone.utc)
    previous_host_statuses = await host_status_repo.get_host_statuses(device)
    previous_port_statuses = await host_status_repo.get_port_statuses(device)

    mapped_endpoints: List[MappedEndpoint] = []
    for endpoint in configured_endpoints:
        endpoint_type = endpoint_types.get(endpoint["type_id"])
        if endpoint_type is None:
            continue
        ip_field = role_field_key(endpoint_type["fields"], endpoint_type["mapping"], "ip")
        ip_value = resolved_value(endpoint["endpoint_data"], ip_field)
        if not ip_value:
            continue
        mapped_endpoints.append(await _build_last_known_endpoint(
            str(ip_value), endpoint, service_types, service_repo, previous_host_statuses, previous_port_statuses, now,
        ))

    return NetworkOverview(
        scanDefinition=NetworkDiscover(networkDefinition="0.0.0.0", subnetMask=32, ports=[]),
        endpoints=mapped_endpoints,
    )


async def build_network_overview(
    device: str,
    scan: NetworkScan,
    endpoint_repo: EndpointRepository,
    service_repo: ServiceRepository,
    host_status_repo: HostStatusRepository,
) -> NetworkOverview:
    endpoint_types = {et["type_id"]: et for et in await endpoint_repo.get_endpoint_types()}
    service_types = {st["type_id"]: st for st in await service_repo.get_service_types()}
    configured_endpoints = await endpoint_repo.get_endpoints(device_id=device)

    now = datetime.now(timezone.utc)
    previous_host_statuses = await host_status_repo.get_host_statuses(device)
    previous_port_statuses = await host_status_repo.get_port_statuses(device)
    new_host_statuses: Dict[str, Tuple[str, datetime]] = {}
    new_port_statuses: Dict[Tuple[str, int], Tuple[str, datetime]] = {}

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

    # A port's default service type (e.g. 21 -> FTP) applies no matter which host it's found on,
    # so this doesn't depend on whether the endpoint itself was identified.
    default_service_types_by_port: Dict[int, Dict[str, Any]] = {}
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

    mapped_endpoints: List[MappedEndpoint] = []
    for host in scan.scanResults:
        ip_str = str(host.ip)
        host_changed_at = _resolve_status_change(previous_host_statuses.get(ip_str), host.status, now)
        new_host_statuses[ip_str] = (host.status, host_changed_at)
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
            # A host matching a type's default IP is automatically instantiated as a real
            # Endpoint - but only if every required field can actually be satisfied (the ip
            # field from the observed value, everything else from the type's own defaults).
            # Otherwise we can't safely auto-create it and it stays a "default" suggestion.
            created_endpoint = None
            ip_field = role_field_key(default_endpoint_type["fields"], default_endpoint_type["mapping"], "ip")
            instance_data = _build_instance_data(default_endpoint_type["fields"], ip_field, ip_str)
            if not _has_missing_required(default_endpoint_type["fields"], instance_data):
                try:
                    created_endpoint = await endpoint_repo.create_endpoint(
                        device_id=device,
                        type_id=default_endpoint_type["type_id"],
                        endpoint_data=instance_data,
                    )
                except Exception:
                    logger.exception(
                        f"failed to auto-create endpoint instance for device <{device}> ip <{ip_str}>"
                    )

            if created_endpoint is not None:
                configured_endpoints_by_ip[ip_str] = created_endpoint
                source = "configured"
                endpoint_id = created_endpoint["endpoint_id"]
                type_id = created_endpoint["type_id"]
                type_label = created_endpoint["type_label"]
                type_description = created_endpoint["type_description"]
                endpoint_data = created_endpoint["endpoint_data"]
                configured_services = await service_repo.get_services(endpoint_id=endpoint_id)
            else:
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

        mapped_ports: List[MappedPort] = []
        for port_str, port_status in host.ports.items():
            try:
                port_num = int(port_str)
            except ValueError:
                continue

            port_changed_at = _resolve_status_change(
                previous_port_statuses.get((ip_str, port_num)), port_status.status, now
            )
            new_port_statuses[(ip_str, port_num)] = (port_status.status, port_changed_at)

            configured_service = configured_services_by_port.get(port_num)
            default_service_type = None if configured_service else default_service_types_by_port.get(port_num)

            if configured_service is not None:
                configured_service_type = service_types.get(configured_service["type_id"])
                mapped_ports.append(MappedPort(
                    port=port_num,
                    status=port_status.status,
                    lastStatusChange=port_changed_at.isoformat(),
                    source="configured",
                    service_id=configured_service["service_id"],
                    type_id=configured_service["type_id"],
                    type_label=configured_service["type_label"],
                    type_description=configured_service["type_description"],
                    service_data=configured_service["service_data"],
                    browser_kind=configured_service_type["browser_kind"] if configured_service_type else None,
                ))
                continue

            if default_service_type is not None:
                # Same auto-create rule as endpoints - only possible once the endpoint itself
                # is a real instance (services are FK'd to an endpoint_id) and required fields
                # can be satisfied from the observed port plus the type's own defaults.
                created_service = None
                if endpoint_id is not None:
                    port_field = role_field_key(default_service_type["fields"], default_service_type["mapping"], "port")
                    instance_data = _build_instance_data(default_service_type["fields"], port_field, port_num)
                    if not _has_missing_required(default_service_type["fields"], instance_data):
                        try:
                            created_service = await service_repo.create_service(
                                endpoint_id=endpoint_id,
                                type_id=default_service_type["type_id"],
                                service_data=instance_data,
                            )
                        except Exception:
                            logger.exception(
                                f"failed to auto-create service instance for endpoint <{endpoint_id}> port <{port_num}>"
                            )

                if created_service is not None:
                    mapped_ports.append(MappedPort(
                        port=port_num,
                        status=port_status.status,
                        lastStatusChange=port_changed_at.isoformat(),
                        source="configured",
                        service_id=created_service["service_id"],
                        type_id=created_service["type_id"],
                        type_label=created_service["type_label"],
                        type_description=created_service["type_description"],
                        service_data=created_service["service_data"],
                        browser_kind=default_service_type["browser_kind"],
                    ))
                else:
                    mapped_ports.append(MappedPort(
                        port=port_num,
                        status=port_status.status,
                        lastStatusChange=port_changed_at.isoformat(),
                        source="default",
                        type_id=default_service_type["type_id"],
                        type_label=default_service_type["label"],
                        type_description=default_service_type["description"],
                        browser_kind=default_service_type["browser_kind"],
                    ))
                continue

            # A port that isn't backed by any configured/default service is only ever reported
            # here while it's actually online (the scanner only ever reports open ports) - so it's
            # always something worth noticing/assigning, never stale noise.
            mapped_ports.append(MappedPort(
                port=port_num,
                status=port_status.status,
                lastStatusChange=port_changed_at.isoformat(),
                source="unidentified",
            ))

        # A configured (assigned) service whose port didn't come back online this cycle needs to
        # distinguish two cases:
        #  - Its port WAS part of this scan's requested port list (`scan.scanDefinition.ports`),
        #    so the host really was checked for it and it simply isn't open anymore - confirmed
        #    offline, persisted, exactly like a host that vanishes entirely. Once assigned, a
        #    service is only ever removed again by deleting it - never by just not showing up.
        #  - Its port wasn't part of this scan's port list at all (e.g. it was only just assigned
        #    and the caller's port list hasn't caught up yet) - fall back to whatever was last
        #    persisted instead of asserting anything about its current state.
        scanned_port_nums = {mapped_port.port for mapped_port in mapped_ports}
        scanned_port_scope = set(scan.scanDefinition.ports)
        for port_num, configured_service in configured_services_by_port.items():
            if port_num in scanned_port_nums:
                continue
            service_type = service_types.get(configured_service["type_id"])
            if port_num in scanned_port_scope:
                port_changed_at = _resolve_status_change(previous_port_statuses.get((ip_str, port_num)), "offline", now)
                new_port_statuses[(ip_str, port_num)] = ("offline", port_changed_at)
                status, changed_at = "offline", port_changed_at
            else:
                previous = previous_port_statuses.get((ip_str, port_num))
                status = previous[0] if previous else "offline"
                changed_at = previous[1] if previous else now
            mapped_ports.append(MappedPort(
                port=port_num,
                status=status,
                lastStatusChange=changed_at.isoformat(),
                source="configured",
                service_id=configured_service["service_id"],
                type_id=configured_service["type_id"],
                type_label=configured_service["type_label"],
                type_description=configured_service["type_description"],
                service_data=configured_service["service_data"],
                browser_kind=service_type["browser_kind"] if service_type else None,
            ))

        mapped_endpoints.append(MappedEndpoint(
            ip=ip_str,
            status=host.status,
            lastStatusChange=host_changed_at.isoformat(),
            source=source,
            endpoint_id=endpoint_id,
            type_id=type_id,
            type_label=type_label,
            type_description=type_description,
            endpoint_data=endpoint_data,
            ports=mapped_ports,
        ))

    # A configured endpoint whose IP is missing from `scan.scanResults` falls into one of two
    # cases, which need different handling:
    #  - Its IP was within the range this scan actually covered (`scan.scanDefinition`), but the
    #    scanner simply didn't find it - the scanner only ever reports hosts it actually finds,
    #    it never lists misses explicitly. That absence is a confirmed "gone now", so persist
    #    offline for the host and its ports (`_build_confirmed_offline_endpoint`).
    #  - Its IP was outside that range entirely (e.g. a manually-assigned endpoint on a different
    #    subnet, or simply not part of this particular scan cycle) - it genuinely wasn't probed at
    #    all, so fall back to its last known persisted status/timestamp, left untouched
    #    (`_build_last_known_endpoint`), rather than wrongly declaring it offline.
    scanned_ips = {str(host.ip) for host in scan.scanResults}
    try:
        scanned_network: Optional[ipaddress.IPv4Network] = ipaddress.IPv4Network(
            f"{scan.scanDefinition.networkDefinition}/{scan.scanDefinition.subnetMask}", strict=False
        )
    except ValueError:
        scanned_network = None

    for ip_str, configured_endpoint in configured_endpoints_by_ip.items():
        if ip_str in scanned_ips:
            continue
        if scanned_network is not None and ipaddress.IPv4Address(ip_str) in scanned_network:
            mapped_endpoints.append(await _build_confirmed_offline_endpoint(
                ip_str, configured_endpoint, service_types, service_repo,
                previous_host_statuses, previous_port_statuses, new_host_statuses, new_port_statuses, now,
            ))
            continue
        mapped_endpoints.append(await _build_last_known_endpoint(
            ip_str, configured_endpoint, service_types, service_repo, previous_host_statuses, previous_port_statuses, now,
        ))

    await host_status_repo.set_host_statuses(device, new_host_statuses)
    await host_status_repo.set_port_statuses(device, new_port_statuses)

    return NetworkOverview(scanDefinition=scan.scanDefinition, endpoints=mapped_endpoints)
