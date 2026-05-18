from ipaddress import IPv4Address
from pydantic import BaseModel
from typing import List, Dict, Union, Literal
from common_schemas import IPv4SubnetStr, IPv4SubnetInt


class DeviceModuleStatus(BaseModel):
    moduleName: str
    moduleStatus: Literal["Connected", "Disconnected", "undefined"]


class NetworkConfig(BaseModel):
    dhcp: bool
    ignore_default_route: bool | None = None
    ip: List[IPv4Address] = []
    subnet: List[IPv4SubnetStr] = []
    gateway: IPv4Address | None = None
    dns: IPv4Address | None = None
    current_ip: IPv4Address | None = None
    current_subnet: IPv4SubnetStr | None = None
    current_gateway: IPv4Address | None = None
    current_dns: Union[IPv4Address, List[IPv4Address]] | None = None
    promiscous_mode: bool
    mtu: str | None = None


class StaticRoutingConfig(BaseModel):
    enabled: bool
    selected: str
    saved: Dict[str, Dict]
    edited: Dict


class VlanConfig(BaseModel):
    ip_address: IPv4Address | None = None
    subnet: IPv4SubnetInt
    gateway: Union[Literal[""], IPv4Address]


class VlansConfig(BaseModel):
    lan1: Dict[str, VlanConfig]
    lan2: Dict[str, VlanConfig]
    lan3: Dict[str, VlanConfig]


class NetworkDict(BaseModel):
    lan1: NetworkConfig
    lan2: NetworkConfig
    lan3: NetworkConfig


class NMShow(BaseModel):
    network: NetworkDict
    static_routing: StaticRoutingConfig
    vlans: VlansConfig


class FirewallRule(BaseModel):
    contents: str


class RuleGroup(BaseModel):
    mgmtd: Dict[str, FirewallRule] | None = None
    policy: str | None = None


class FilterSection(BaseModel):
    mgmtd: Dict[str, FirewallRule]
    forward: Dict[str, FirewallRule] | None = None
    input: Dict[str, FirewallRule] | None = None
    output: Dict[str, FirewallRule] | None = None
    policy: str | None = None


class NatSection(BaseModel):
    postrouting: Dict[str, FirewallRule] | None = None
    prerouting: Dict[str, FirewallRule] | None = None
    policy: str | None = None


class Inet(BaseModel):
    filter: FilterSection


class Ip(BaseModel):
    nat: NatSection


class SavedPreset(BaseModel):
    inet: Inet
    ip: Ip


class Firewall(BaseModel):
    enabled: bool
    saved: Dict[str, SavedPreset]
    selected: str


class FWShow(BaseModel):
    firewall: Firewall


class SEMSCheck(BaseModel):
    message: str


class LanIpStatic(BaseModel):
    interface: Literal["lan2", "lan3"]
    ip: IPv4Address
    subnet: IPv4SubnetInt
    gateway: IPv4Address | None = None
