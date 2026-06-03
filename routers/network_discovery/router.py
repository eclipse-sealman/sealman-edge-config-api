from typing import Union
from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device
from fastapi import Depends

from routers.base_api_router import BaseAPIRouter
from .schemas import NetworkScan, NetworkDiscover
from common_schemas import DirectMethod
from routers.network_discovery.routes.post_network_discover2 import post_network_discover2 as _post_network_discover2
from routers.network_discovery.routes.get_network_topology import get_network_topology as _get_network_topology


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
