from ipaddress import IPv4Address
from pydantic import BaseModel
from typing import Annotated, Dict, List, Literal, Optional
from common_schemas import IPv4SubnetInt
from routers.schemas import ResolvedField
from routers.service.schemas import BrowserKind


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


MappedSource = Literal["configured", "default", "unidentified"]


class MappedPort(BaseModel):
    port: int
    status: Literal["online", "offline", "unknown"]
    lastStatusChange: Optional[str] = None
    source: MappedSource
    service_id: Optional[str] = None
    type_id: Optional[str] = None
    type_label: Optional[str] = None
    type_description: Optional[str] = None
    service_data: Optional[Dict[str, ResolvedField]] = None
    browser_kind: Optional[BrowserKind] = None


class MappedEndpoint(BaseModel):
    ip: str
    status: Literal["online", "offline", "unknown"]
    lastStatusChange: Optional[str] = None
    source: MappedSource
    endpoint_id: Optional[str] = None
    type_id: Optional[str] = None
    type_label: Optional[str] = None
    type_description: Optional[str] = None
    endpoint_data: Optional[Dict[str, ResolvedField]] = None
    ports: List[MappedPort]


class NetworkOverview(BaseModel):
    scanDefinition: NetworkDiscover
    endpoints: List[MappedEndpoint]


class NetworkRange(BaseModel):
    # Both absent when there's no per-device evidence yet (no configured endpoints, nothing
    # previously persisted) - `suggestedIps` alone can still be non-empty in that case.
    networkDefinition: Optional[Annotated[str, IPv4Address]] = None
    subnetMask: Optional[IPv4SubnetInt] = None
    # Endpoint types' default IPs, probed individually on every scan - global suggestions, never
    # persisted (see get_network_scan_range.py).
    suggestedIps: List[str] = []
