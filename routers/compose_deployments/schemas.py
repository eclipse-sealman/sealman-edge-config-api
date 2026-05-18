from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, constr, conint


DEFAULT_TILE_ICON=("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb"
                   "3g9IjAgMCAxMjggMTI4Ij48cmVjdCB4PSIyMCIgeT0iNjAiIHdpZHRoPSIyMCIgaGVpZ2h0PSI0OCIgZmlsbD0i"
                   "IzRjYWY1MCIvPjxyZWN0IHg9IjU0IiB5PSI0MCIgd2lkdGg9IjIwIiBoZWlnaHQ9IjY4IiBmaWxsPSIjMjE5NmY"
                   "zIi8+PHJlY3QgeD0iODgiIHk9IjI0IiB3aWR0aD0iMjAiIGhlaWdodD0iODQiIGZpbGw9IiNmZjk4MDAiLz48L3"
                   "N2Zz4=")

# allows <port>:<port>(/<protocol>)  optional in ()
# e.g.:
# 80:80/tcp
# 5050:5050
# 20:5623/udp
PortMapping = constr(
    pattern=r"^(?:[1-9]\d{0,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5]):(?:[1-9]\d{0,3}|[1-5]\d{4}|"
            r"6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])(?:\/(?:tcp|udp))?$"
)


Port = conint(ge=1, le=65535)


class ServiceConfig(BaseModel):
    name: str
    image: str
    serving_http_port: Port = 80 # type: ignore

    exposed_ports: List[PortMapping] = Field(default_factory=list) # type: ignore
    env: List[str] = Field(default_factory=list)
    volumes: List[str] = Field(default_factory=list)

    tile_enabled: bool = True
    tile_title: str = "Unnamed App"
    tile_description: str = "Empty Description"
    tile_group: str = "Undefined"
    tile_icon: str = DEFAULT_TILE_ICON


class ComposeRequest(BaseModel):
    description: Optional[str] = None
    services: List[ServiceConfig]


class ComposeResponse(BaseModel):
    name: str
    description: Optional[str] = None
    compose: dict
    
class DeploymentListItem(BaseModel):
    name: str
    description: Optional[str] = None
    landing_page: Optional[bool] = False
    created_at: datetime
    updated_at: datetime


class DeploymentDetailResponse(BaseModel):
    name: str
    description: Optional[str] = None
    request: Dict
    compose: Dict
    sems_compose: Dict
    
DeploymentResponseUnion = Union[
    DeploymentDetailResponse,
    Dict
]


class ActivateDeploymentResponse(BaseModel):
    active_deployment: str
    status: str


class ActiveDeploymentResponse(BaseModel):
    active_deployment: Optional[str]


class MessageResponse(BaseModel):
    message: str

class ActivateDeploymentRequest(BaseModel):
    name: str
