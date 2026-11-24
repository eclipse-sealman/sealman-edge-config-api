from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field

TEMPLATE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._\-]{0,127}$"
MODULE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$"

JsonScalar = Union[str, int, float, bool]
# A template body is an object, a block body an object or an array (checked by lint per kind).
Body = Union[Dict[str, Any], List[Any]]
Kind = Literal["template", "block"]
ModuleName = Annotated[str, Field(pattern=MODULE_NAME_PATTERN)]


_ENDPOINTS_DESCRIPTION = "endpoint slot -> endpoint_id of the device, for ${endpoint[slot].*} (${endpoint.*} is slot 'endpoint')"


_SERVICES_DESCRIPTION = ("endpoint slot (keyed like `endpoints`) -> service_id of the endpoint chosen for that slot, "
                         "for ${service[slot].*}")

_ROWS_DESCRIPTION = "endpoint slot (keyed like `endpoints`) -> {list field: id of the chosen row}"


_LISTS_DESCRIPTION = "list key (as in inputs.lists) -> its entries, in order; each entry renders its block under its name"


class ListEntry(BaseModel):
    name: str
    block_id: int


class VariableDecl(BaseModel):
    name: str = Field(..., max_length=64)
    type: Literal["string", "text", "number", "integer", "boolean", "enum", "file"] = "string"
    required: bool = True
    options: Optional[List[JsonScalar]] = None
    secret: Optional[bool] = Field(None, description="file variables only: the file is never shown or downloaded")
    default: Optional[Dict[str, Any]] = Field(None, description="file variables only: reference of the default file")
    label: Optional[str] = Field(None, max_length=128)
    description: Optional[str] = Field(None, max_length=1024)


class SlotDecl(BaseModel):
    """Which endpoint and service types may be chosen for an endpoint slot (empty list: any type)."""
    name: str = Field(..., max_length=64)
    endpoint_types: List[str] = []
    service_types: List[str] = []


class Finding(BaseModel):
    path: str
    level: Literal["error", "warning", "hint"]
    code: str
    message: str


class TemplateContent(BaseModel):
    body: Body
    variables: List[VariableDecl] = []
    slots: List[SlotDecl] = []
    comment: Optional[str] = Field(None, max_length=1024)


class ScopeRef(BaseModel):
    id: UUID
    name: str


class TemplateGroupCreate(BaseModel):
    name: str = Field(..., pattern=TEMPLATE_NAME_PATTERN)
    description: Optional[str] = Field(None, max_length=2048)
    module_names: List[ModuleName] = Field(..., min_length=1, description="exact module names, e.g. 'seal-app-net-discover'")
    scope_ids: List[UUID] = Field([], description="devices matching one of these scopes may use the templates; none: every device")


class TemplateGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, pattern=TEMPLATE_NAME_PATTERN)
    description: Optional[str] = Field(None, max_length=2048)
    module_names: Optional[List[ModuleName]] = Field(None, min_length=1)
    scope_ids: Optional[List[UUID]] = None


class TemplateGroupOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    module_names: List[str]
    scopes: List[ScopeRef]
    template_count: int
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TemplateCreate(TemplateContent):
    name: str = Field(..., pattern=TEMPLATE_NAME_PATTERN)
    description: Optional[str] = Field(None, max_length=2048)
    group_id: int
    kind: Kind = Field("template", description="template: a module configuration; block: a reusable part")


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, pattern=TEMPLATE_NAME_PATTERN)
    description: Optional[str] = Field(None, max_length=2048)
    group_id: Optional[int] = None
    archived: Optional[bool] = None


class TemplateDuplicate(BaseModel):
    name: str = Field(..., pattern=TEMPLATE_NAME_PATTERN)
    version: Optional[int] = Field(None, ge=1, description="version to copy, latest if omitted")
    description: Optional[str] = Field(None, max_length=2048)
    group_id: Optional[int] = Field(None, description="group of the copy, that of the source if omitted")


class TemplateOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    kind: Kind = "template"
    group_id: int
    group_name: str
    module_names: List[str]
    scopes: List[ScopeRef] = []
    derived_from_template_id: Optional[int] = None
    derived_from_version: Optional[int] = None
    archived: bool
    archived_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    latest_version: Optional[int] = None


class TemplateVersionOut(BaseModel):
    template_id: int
    version: int
    body: Body
    variables: List[VariableDecl]
    slots: List[SlotDecl] = []
    comment: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    references: Dict[str, List[str]] = {}
    inputs: Dict[str, Any] = Field({}, description="variables, endpoint slots, device fields and block copies")


class TemplateVersionSummary(BaseModel):
    template_id: int
    version: int
    comment: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime


class LintRequest(BaseModel):
    body: Body
    variables: List[VariableDecl] = []
    slots: List[SlotDecl] = []
    kind: Kind = "template"
    group_id: Optional[int] = Field(
        None, description="group the template belongs to: its blocks are loaded and checked; omitted, block "
                          "references are only syntax-checked")


class PreviewRequest(LintRequest):
    values: Dict[str, Any] = {}
    device: Optional[Dict[str, Any]] = Field(None, description="sample device metadata for ${device.*}")
    device_id: Optional[str] = Field(
        None, description="render with the metadata of this real device (within the group's scopes) instead of "
                          "`device`; needs `group_id`")
    endpoints: Optional[Dict[str, str]] = Field(
        None, description="with `device_id`: " + _ENDPOINTS_DESCRIPTION + "; omitted, endpoint placeholders stay as they are")
    lists: Dict[str, List[ListEntry]] = Field({}, description=_LISTS_DESCRIPTION)
    services: Dict[str, str] = Field({}, description="with `device_id` and `endpoints`: " + _SERVICES_DESCRIPTION)
    rows: Dict[str, Dict[str, str]] = Field({}, description="with `device_id` and `endpoints`: " + _ROWS_DESCRIPTION)


class ExampleDevice(BaseModel):
    device_id: str
    device_data: Dict[str, Any]


class LintResponse(BaseModel):
    ok: bool
    findings: List[Finding]
    references: Dict[str, List[str]]
    inputs: Dict[str, Any] = {}


class RenderRequest(BaseModel):
    template_id: int
    values: Dict[str, Any] = {}
    endpoints: Dict[str, str] = Field({}, description=_ENDPOINTS_DESCRIPTION)
    lists: Dict[str, List[ListEntry]] = Field({}, description=_LISTS_DESCRIPTION)
    services: Dict[str, str] = Field({}, description=_SERVICES_DESCRIPTION)
    rows: Dict[str, Dict[str, str]] = Field({}, description=_ROWS_DESCRIPTION)


class ApplyRequest(RenderRequest):
    comment: Optional[str] = Field(None, max_length=1024)


class RenderResponse(BaseModel):
    ok: bool
    template_id: Optional[int] = None
    version: Optional[int] = None
    config: Optional[Body] = None
    findings: List[Finding]


class RevisionOut(BaseModel):
    id: int
    device_id: str
    module_name: str
    config_id: str
    blob_name: str
    parent_revision_id: Optional[int] = None
    source: str
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    values: Optional[Dict[str, Any]] = None
    content_hash: Optional[str] = None
    actor: Optional[str] = None
    comment: Optional[str] = None
    reapplied_from_id: Optional[int] = None
    created_at: datetime
    current: bool = False


class RevisionDetail(RevisionOut):
    content: Optional[Any] = None
    content_available: bool = True


class RevisionList(BaseModel):
    current_config_id: Optional[str] = None
    revisions: List[RevisionOut]


class ReapplyRequest(BaseModel):
    comment: Optional[str] = Field(None, max_length=1024)


class ApplyResponse(BaseModel):
    configId: str
    revision: Optional[RevisionOut] = None
    findings: List[Finding] = []


class DeviceEndpointOption(BaseModel):
    endpoint_id: str
    type_id: str
    type_label: str
    name: Optional[str] = None
    ip: Optional[str] = None
    fields: Dict[str, Any]


class DeviceServiceOption(BaseModel):
    service_id: str
    type_id: str
    type_label: str
    name: Optional[str] = None
    port: Optional[Any] = None
    fields: Dict[str, Any]
