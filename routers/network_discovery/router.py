from typing import List, Optional, Union
from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from fastapi import Depends

from routers.base_api_router import BaseAPIRouter
from .schemas import NetworkScan, NetworkDiscover, NetworkOverview, NetworkRange
from common_schemas import DirectMethod
from routers.network_discovery.routes.post_network_discover2 import post_network_discover2 as _post_network_discover2
from routers.network_discovery.routes.get_network_topology import get_network_topology as _get_network_topology
from routers.network_discovery.routes.post_network_overview import build_network_overview as _build_network_overview
from routers.network_discovery.routes.get_network_scan_ports import get_network_scan_ports as _get_network_scan_ports
from routers.network_discovery.routes.get_network_scan_range import get_network_scan_range as _get_network_scan_range
from routers.network_discovery.routes.scan_ranges_list import (
    get_network_scan_ranges as _get_network_scan_ranges,
    put_network_scan_ranges as _put_network_scan_ranges,
)
from db.repos.endpoint import EndpointRepository
from db.repos.service import ServiceRepository
from db.repos.network_range import NetworkRangeRepository
from db.repos.extra_network_range import ExtraNetworkRangeRepository
from db.repos.host_status import HostStatusRepository
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


@network_discovery.get("/{device}/network/scan-ports", tags=["Network"],
                       response_model=List[int])
async def get_network_scan_ports(device: str,
                                  endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
                                  service_repo: ServiceRepository = Depends(get_repository(ServiceRepository)),
                                  _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_network_scan_ports(device, endpoint_repo, service_repo)


@network_discovery.get("/{device}/network/scan-range", tags=["Network"],
                       response_model=Optional[NetworkRange])
async def get_network_scan_range(device: str,
                                  endpoint_repo: EndpointRepository = Depends(get_repository(EndpointRepository)),
                                  network_range_repo: NetworkRangeRepository = Depends(get_repository(NetworkRangeRepository)),
                                  _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_network_scan_range(device, endpoint_repo, network_range_repo)


@network_discovery.get("/{device}/network/scan-ranges", tags=["Network"],
                       response_model=List[NetworkRange])
async def get_network_scan_ranges(device: str,
                                   repo: ExtraNetworkRangeRepository = Depends(get_repository(ExtraNetworkRangeRepository)),
                                   _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_network_scan_ranges(device, repo)


@network_discovery.put("/{device}/network/scan-ranges", tags=["Network"],
                       response_model=List[NetworkRange])
async def put_network_scan_ranges(device: str, ranges: List[NetworkRange],
                                   repo: ExtraNetworkRangeRepository = Depends(get_repository(ExtraNetworkRangeRepository)),
                                   _ = Depends(ABACPermissionCheck(Device.NETWORK_WRITE))):
    return await _put_network_scan_ranges(device, ranges, repo)
