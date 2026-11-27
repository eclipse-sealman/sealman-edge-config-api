from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional


# ---------- Templates ----------

class TemplateInfo(BaseModel):
    id: int
    name: str
    selected: bool


class TemplateListResponse(BaseModel):
    templates: List[TemplateInfo]


class SelectedTemplatesRequest(BaseModel):
    templates: List[str]


# ---------- Device Template Variables ----------

class TemplateVariableListResponse(BaseModel):
    variables: Dict[str, str]


class SetTemplateVariableRequest(BaseModel):
    value: str


class TestDeviceConfigResponse(BaseModel):
    valid: bool
    message: str


class IoTHubInfo(BaseModel):
    hostname: Optional[str] = None


class DeviceCreationReadiness(BaseModel):
    ready: bool
    missing: List[str]


# ---------- Base Deployment ----------

class BaseDeploymentModule(BaseModel):
    name: str
    image: str
    restart_policy: Literal["always", "never", "on-failure", "on-unhealthy"] = "always"
    status: Literal["running", "stopped"] = "running"
    # Docker create options as a JSON string; empty for none.
    create_options: str = ""
    env: Dict[str, str] = Field(default_factory=dict)
    # Desired properties of the module twin.
    twin: Dict[str, Any] = Field(default_factory=dict)


class BaseDeploymentUpdate(BaseModel):
    modules: List[BaseDeploymentModule]
    # Environment of the edgeHub system module; omitted keeps the saved one.
    edge_hub_env: Optional[Dict[str, str]] = None


class BaseDeploymentResponse(BaseModel):
    modules: List[BaseDeploymentModule]
    edge_hub_env: Dict[str, str]
    manifest: Dict[str, Any]
    # False while the shipped default is shown and nothing has been saved yet.
    stored: bool


# ---------- Platform Functions ----------

class PlatformFunctions(BaseModel):
    network_scan_enabled: bool
    azure_iot_edge_enabled: bool
