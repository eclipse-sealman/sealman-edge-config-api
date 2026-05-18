from pydantic import BaseModel
from typing import List, Literal

class Metadata(BaseModel):
    createdAt: str
    updatedAt: str

class ServiceStatus(BaseModel):
    name: str
    status: Literal["online", "offline", "unknown"]
    port: int

class EndpointStatus(BaseModel):
    status: Literal["online", "offline", "unknown"]
    startTime: str | None = None
    serviceStatuses: List[ServiceStatus] | None = None

class Service(BaseModel):
    name: str
    port: int
    protocol: str

class EndpointSpec(BaseModel):
    ip: str #TODO: Perhaps use IPv4Address type if we want to enforce IP format
    services: List[Service] | None = None

class Connection(BaseModel):
    id: str
    source: str
    target: str
    sourceHandle: str
    targetHandle: str
    type: str | None = None

class TopologyMeasurement(BaseModel):
    width: int
    height: int

class TopologyPosition(BaseModel):
    x: float
    y: float

class TopologyData(BaseModel):
    nodeType: str
    position: TopologyPosition
    measured: TopologyMeasurement

class IsolatedEndpoint(BaseModel):
    id: str
    name: str | None = None
    ip: str
    builtIn: str | None = None
    topologyData: TopologyData

class MachineEndpoint(BaseModel):
    name: str | None = None
    ip: str

class Machine(BaseModel):
    id: str
    machineNumber: str | None = None
    name: str | None = None
    endpoints: List[MachineEndpoint] | None = None
    topologyData: TopologyData

class Line(BaseModel):
    edgeDeviceId: str
    name: str | None = None
    lineNumber: str | None = None
    metadata: Metadata | None = None
    machines: List[Machine] | None = None
    isolatedEndpoints: List[IsolatedEndpoint] | None = None
    connections: List[Connection] | None = None