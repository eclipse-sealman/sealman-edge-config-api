from datetime import datetime
from enum import Enum
from pydantic import BaseModel, RootModel
from typing import Any, Union, Literal, List, Dict

class DeviceMetadataEntry(BaseModel):
    value: Any
    source: Literal["platform", "device"]
    
    class Config:
        from_attributes = True

class DeviceMetadataResponse(BaseModel):
    deviceId: str
    deviceMetadata: Dict[str, DeviceMetadataEntry]
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Config:
        from_attributes = True

class DeviceStatus(DeviceMetadataResponse):
    deviceStatus: Literal["Connected", "Disconnected", "Unknown"]
    lastSeenAt: str | None = None
    lastSeenInRange: bool | None = None
    lastSeenRangeInDays: int | None = None
    
class DeviceStatusWithConnection(DeviceStatus):
    iotEdgeRuntime: str
    iotHub: str
    sems: str
    vpn: str

class DeviceConnectionInfo(BaseModel):
    deviceName: str
    iotEdgeRuntime: str
    iotHub: str
    sems: str
    vpn: str

class DeviceStatusList(RootModel[List[DeviceStatus]]):
    pass

class DeviceStatusWithConnectionList(RootModel[List[DeviceStatusWithConnection]]):
    pass

class DeviceConnectionInfoList(RootModel[List[DeviceConnectionInfo]]):
    pass
class Deployment(BaseModel):
    id: str
    targetCondition: str
class ResponseDeploymentList(RootModel[List[Deployment]]):
    pass

class MetricValue(BaseModel):
    timestamp: datetime
    value: float

class DeviceMetric(BaseModel):
    deviceId: str
    mem: List[MetricValue]
    mem_percent: List[MetricValue]

class DeviceMetricList(RootModel[List[DeviceMetric]]):
    pass

class Aggregation(str, Enum):
    latest = "latest"
    values = "values"

class DeploymentTag(BaseModel):
    deployment: str | None = None

class ModuleStatus(BaseModel):
    moduleName: str
    moduleId: str
    connectionState: Literal["Connected", "Disconnected"]
    moduleType: Literal["iotedge", "api", "compose"]
    deploymentType: Literal["base", "sems"]
    status: Literal[
        "running", "backoff", "failed", "unknown", "unhealthy", "stopped",
        "runtime_online", "runtime_offline",
        "running:grey", "backoff:grey", "failed:grey", "unknown:grey", "unhealthy:grey", "stopped:grey",
    ]
    version: str | None = None

class DeviceModuleList(RootModel[List[ModuleStatus]]):
    pass

class DeviceConnectionStatus(BaseModel):
    iotEdgeRuntime: Literal["Connected", "Disconnected", "Unknown"] = "Unknown"
    iotHub: Literal["Connected", "Disconnected", "Unknown"] = "Unknown"
    sems: Literal["Connected", "Disconnected", "Unknown"] = "Unknown"
    # vpn:  Literal["Connected", "Disconnected", "Unknown"] = "Unknown"

class DeviceModuleMethodReq(BaseModel):
    methodName: str
    methodPayload: dict

class ModuleDeploymentStatus(BaseModel):
    deviceId: str
    deploymentId: str | None = None
    priority: int | None = None
    targeted: bool = False
    applied: bool = False
    success: bool = False
