from typing import List, Optional, Union
from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from fastapi import Depends

from routers.base_api_router import BaseAPIRouter
from .schemas import NetworkScan, NetworkDiscover, NetworkOverview, NetworkRange, DeviceScanPortsAdd, DefaultScanPortCreate
from common_schemas import DirectMethod
from routers.network_discovery.routes.post_network_discover2 import post_network_discover2 as _post_network_discover2
from routers.network_discovery.routes.get_network_topology import get_network_topology as _get_network_topology
from routers.network_discovery.routes.post_network_overview import (
    build_network_overview as _build_network_overview,
    build_last_known_network_overview as _build_last_known_network_overview,
)
from routers.network_discovery.routes.get_network_scan_ports import get_network_scan_ports as _get_network_scan_ports
from routers.network_discovery.routes.get_network_scan_range import get_network_scan_range as _get_network_scan_range
from routers.network_discovery.routes.post_device_scan_ports import post_device_scan_ports as _post_device_scan_ports
from routers.network_discovery.routes.default_scan_ports import (
    get_default_scan_ports as _get_default_scan_ports,
    add_default_scan_port as _add_default_scan_port,
    remove_default_scan_port as _remove_default_scan_port,
)
from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from db.repos.network_range import NetworkRangeRepository
from db.repos.host_status import HostStatusRepository
from db.repos.scan_ports import ScanPortsRepository
from db.session import get_repository


network_discovery = BaseAPIRouter()

@network_discovery.post("/{device}/network/discover", tags=["Network Discovery"],
                        response_model=DirectMethod[NetworkScan])
async def post_network_discover(device: str, network_discover: NetworkDiscover,
                                 auth_context = Depends(ABACPermissionCheck(Device.NETWORK_DISCOVER))):
    return await _post_network_discover2(device, network_discover, auth_context)


@network_discovery.get("/{device}/network/topology", tags=["Network Discovery"],
                       response_model=Union[NetworkScan, None])
async def get_network_topology(device: str,
                                _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_network_topology(device)


@network_discovery.post("/{device}/network/overview", tags=["Network"],
                        response_model=NetworkOverview)
async def post_network_overview(device: str, scan: NetworkScan,
                                 endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
                                 service_repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
                                 host_status_repo: HostStatusRepository = Depends(get_repository(HostStatusRepository)),
                                 _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _build_network_overview(device, scan, endpoint_repo, service_repo, host_status_repo)


@network_discovery.get("/{device}/network/last-known", tags=["Network"],
                       response_model=NetworkOverview)
async def get_network_last_known(device: str,
                                  endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
                                  service_repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
                                  host_status_repo: HostStatusRepository = Depends(get_repository(HostStatusRepository)),
                                  _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _build_last_known_network_overview(device, endpoint_repo, service_repo, host_status_repo)


@network_discovery.get("/{device}/network/scan-ports", tags=["Network"],
                       response_model=List[int])
async def get_network_scan_ports(device: str,
                                  endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
                                  service_repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
                                  scan_ports_repo: ScanPortsRepository = Depends(get_repository(ScanPortsRepository)),
                                  _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_network_scan_ports(device, endpoint_repo, service_repo, scan_ports_repo)


@network_discovery.post("/{device}/network/scan-ports", tags=["Network"],
                        response_model=List[int])
async def post_network_scan_ports(device: str, body: DeviceScanPortsAdd,
                                   scan_ports_repo: ScanPortsRepository = Depends(get_repository(ScanPortsRepository)),
                                   _ = Depends(ABACPermissionCheck(Device.NETWORK_WRITE))):
    return await _post_device_scan_ports(device, body, scan_ports_repo)


@network_discovery.get("/{device}/network/scan-range", tags=["Network"],
                       response_model=Optional[NetworkRange])
async def get_network_scan_range(device: str,
                                  endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
                                  network_range_repo: NetworkRangeRepository = Depends(get_repository(NetworkRangeRepository)),
                                  _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_network_scan_range(device, endpoint_repo, network_range_repo)


# Global settings, not scoped to any one device - see routers/service/router.py for the same
# `device_path=None` pattern used for service types, which this list lives alongside in the
# Settings UI.
_scan_ports_read = Depends(ABACPermissionCheck(Device.ENDPOINT_READ, device_path=None))
_scan_ports_write = Depends(ABACPermissionCheck(Device.ENDPOINT_WRITE, device_path=None))


@network_discovery.get("/network/default-scan-ports", tags=["Network"],
                       response_model=List[int])
async def get_default_scan_ports(scan_ports_repo: ScanPortsRepository = Depends(get_repository(ScanPortsRepository)),
                                  _auth=_scan_ports_read):
    return await _get_default_scan_ports(scan_ports_repo)


@network_discovery.post("/network/default-scan-ports", tags=["Network"],
                        response_model=List[int])
async def post_default_scan_port(body: DefaultScanPortCreate,
                                  scan_ports_repo: ScanPortsRepository = Depends(get_repository(ScanPortsRepository)),
                                  _auth=_scan_ports_write):
    return await _add_default_scan_port(body.port, scan_ports_repo)


@network_discovery.delete("/network/default-scan-ports/{port}", tags=["Network"],
                          response_model=List[int])
async def delete_default_scan_port(port: int,
                                    scan_ports_repo: ScanPortsRepository = Depends(get_repository(ScanPortsRepository)),
                                    _auth=_scan_ports_write):
    return await _remove_default_scan_port(port, scan_ports_repo)
