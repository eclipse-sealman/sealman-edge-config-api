from pydantic import BaseModel
from typing import Optional, Dict, Any, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate

# Not constrained to a fixed set of values - the frontend maintains an extensible registry of
# browser kinds (see registerBrowser()), so new kinds can be added there without a backend change.
BrowserKind = str


class ServiceTypeCreate(BaseModel):
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}
    browser_kind: Optional[BrowserKind] = None
    # {field_key: role}. Role: "port". Checked against `fields` by db/type_roles.py (422).
    mapping: Dict[str, str] = {}


class ServiceTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None
    # Omitted leaves it unchanged; an explicit null clears it back to "no browse action"
    # (the router tells them apart with model_fields_set).
    browser_kind: Optional[BrowserKind] = None
    # Replaces the whole mapping when sent. When omitted, the stored roles stay, except those
    # whose field `fields` removes.
    mapping: Optional[Dict[str, str]] = None


class ServiceTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    mapping: Dict[str, str]
    browser_kind: Optional[BrowserKind]
    created_at: datetime
    updated_at: datetime


class ServiceCreate(BaseModel):
    type_id: str
    service_data: Dict[str, Any]


class ServiceUpdate(BaseModel):
    service_data: Dict[str, Optional[Any]]


class ServiceResponse(BaseModel):
    service_id: str
    endpoint_id: str
    type_id: str
    type_label: str
    type_description: Optional[str]
    service_data: Dict[str, ResolvedField]
    created_at: datetime
    updated_at: datetime
