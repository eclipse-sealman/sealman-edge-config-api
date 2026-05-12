from ipaddress import IPv4Address
from pydantic import BaseModel
from typing import Annotated, Dict, List, Literal
from common_schemas import IPv4SubnetInt


class NetworkDiscover(BaseModel):
    networkDefinition: Annotated[str, IPv4Address]
    ports: List[int]
    subnetMask: IPv4SubnetInt


class PortStatus(BaseModel):
    status: Literal["online", "offline", "unknown"]
    lastStatusChange: str | None = None


class EndpointStatus(BaseModel):
    ip: IPv4Address
    status: Literal["online", "offline", "unknown"]
    lastStatusChange: str | None = None
    ports: Dict[str, PortStatus]


class NetworkScan(BaseModel):
    scanResults: List[EndpointStatus]
    scanDefinition: NetworkDiscover
