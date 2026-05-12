from pydantic import BaseModel
from typing import List, Optional


# ---------- Templates ----------

class TemplateInfo(BaseModel):
    id: int
    name: str
    selected: bool


class TemplateListResponse(BaseModel):
    templates: List[TemplateInfo]


class SelectedTemplatesRequest(BaseModel):
    templates: List[str]


# ---------- Device Endpoint Types ----------

class EndpointType(BaseModel):
    name: str
    description: Optional[str] = None
    defaultIP: Optional[str] = None


class EndpointTypeList(BaseModel):
    types: List[EndpointType]


class EndpointTypeUpdateRequest(BaseModel):
    types: List[EndpointType]


# ---------- Services ----------

class ServiceConfig(BaseModel):
    deviceEndpointServiceName: str
    description: Optional[str] = None
    defaultPort: Optional[str] = None


class ServiceListResponse(BaseModel):
    services: List[ServiceConfig]


class ServiceUpdateRequest(BaseModel):
    services: List[ServiceConfig]


# ---------- Platform Metadata Keys ----------

class AddMetadataKeyRequest(BaseModel):
    key: str


class MetadataKeysResponse(BaseModel):
    keys: List[str]
