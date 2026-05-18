from datetime import datetime
from ipaddress import IPv4Address
from pydantic import BaseModel, Field, RootModel
from typing import Annotated, Any, List, Dict, Union, Literal ,Optional
from common_schemas import IPv4SubnetStr
from enum import Enum


class DeviceTags(BaseModel):
    deviceTags: Dict[str, Union[str, dict, None]]


class SemsAccessTag(BaseModel):
    toString: str
    id: str
    createdAt: str


class SemsAccessTags(RootModel[List[SemsAccessTag]]):
    pass


class SemsTimelog(BaseModel):
    toString: str
    id: str
    createdAt: str


class SemsTemplate(BaseModel):
    toString: str
    id: int
    createdAt: str
    updatedAt: str


class SemsInfo2(BaseModel):
    enabled: bool
    lastSeenAt: str
    hardwareVersion: str
    updateFirmware: bool
    semsTemplate: str
    firmwareVersion: str
    deviceTypeId: int
    deviceTypeName: str
    template: SemsTemplate | None = None
    description: str
    variables: List


class EdgeCommandUpdateStatus(BaseModel):
    cmdName: str
    status: Literal["pending", "success", "error", "critical", "expired"]
    created: str
    updated: str


class SemsFirmwareStatus(BaseModel):
    deviceFirmwareVersion: str
    deviceEnabled: bool
    deviceTemplate: str
    deviceLastSeen: str
    deviceHardwareVersion: str
    firmwareUpdateScheduled: bool
    configUpdateScheduled: bool
    edgeCommandStatus: List[EdgeCommandUpdateStatus]


class TemplateVariable(BaseModel):
    name: str
    variableValue: str


class LanConfigDeviceUpdate(BaseModel):
    serialNumber: str
    description: str
    template: int
    updateFirmware: bool
    updateConfig: bool
    getConfig: bool
    accessTags: List[str]
    enabled: bool
    variables: List[TemplateVariable]

class GeneratedDeviceConfig(RootModel[Dict[Any, Any]]):
    pass


class LanInterface(BaseModel):
    dhcp: bool
    ip: IPv4Address | None = None
    gw: IPv4Address | None = None
    dns: IPv4Address | None = None
    subnet: IPv4SubnetStr | None = None



class SemsUpdateLan(BaseModel):
    lan2: LanInterface | None = None
    lan3: LanInterface | None = None


class SemsUpdateLanRes(BaseModel):
    updatedInterfaces: Dict[Literal["lan2", "lan3"], LanInterface]


class InterfaceConfig(BaseModel):
    lan1: LanInterface
    lan2: LanInterface
    lan3: LanInterface


class SemsGetLan(BaseModel):
    interfaceConfig: InterfaceConfig


class CellularState(str, Enum):
    on = "on"
    off = "off"


class CellularInterface(BaseModel):
    apn: str | None = None
    pin: str | None = None
    access_number: str | None = None
    auth_method: str | None = None
    username: str | None = None
    password: str | None = None
    state: CellularState | None = None


class ConfigCellular(BaseModel):
    cellular: bool
    interface: CellularInterface


class NatRule(BaseModel):
    name: Annotated[str, Field(pattern=r'^[a-zA-Z0-9_]+$')]
    extIp: IPv4Address
    intIp: IPv4Address


class NatConfig(BaseModel):
    nat_enabled: bool
    nat_rules: List[NatRule] = []

    # Default the value to an empty string as opposed to None
    def get_nat_rules(self) -> List[NatRule]:
        if self.nat_rules is None:
            return []
        return self.nat_rules    

class DeviceSecretInformation(BaseModel):
    deviceTypeHasAuthSecret: bool
    forceRenewal: bool = False
    secretValueRenewAfterDays: int = 0
    error: str | None = None
    id: int | None = None
    secretCreatedAt: datetime | None = None
    secretUpdatedAt: datetime | None = None


class DeviceSecretValue(BaseModel):
    id: int
    secretValue: str


class DefinedVariable(BaseModel):
    name: str
    variableValue: str

class DeviceConfig(BaseModel):
    configurationVersion: str
    firmwareVersion: str
    minimumFirmareVersion: str | None # there is a typo in the key
    accessTagIds: List[int] = Field(default_factory=list)
    definedVariables: List[DefinedVariable] = Field(default_factory=list)
    reinstallConfig: bool
    requestConfigData: bool
    enabled: bool
    staging: bool
    note: str | None
    lastModifiedDate: datetime


class DefaultSmartEMSTemplate(BaseModel):
    deviceType: str
    hardwareVersion: str
    defaultConfig: DeviceConfig
    templateName: str

class ApplyDefaultTemplateResult(BaseModel):
    deviceId: str
    deviceTemplateName: str
    initTime: str

class PortForwardingRule(BaseModel):
    name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Rule name (ASCII, no spaces, no special characters)"
    )

    interface: Literal["lan1", "lan2", "lan3"]

    srcPort: int = Field(
        ...,
        ge=1,
        le=65535,
        description="Source TCP port"
    )

    destAddr: IPv4Address

    destPort: int = Field(
        ...,
        ge=1,
        le=65535,
        description="Destination TCP port"
    )


class PortForwardingConfig(BaseModel):
    rules: Optional[List[PortForwardingRule]] = []

    def get_rules(self) -> List[PortForwardingRule]:
        if self.rules is None:
            return []
        return self.rules
