from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate


class EndpointTypeCreate(BaseModel):
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}
    # {field_key: role}. Roles: "ip", "name". Checked against `fields` by db/type_roles.py
    # (422 on violation).
    mapping: Dict[str, str] = {}
    # {extension: [field_key, ...]}: declared fields of enabled extensions to copy onto the new
    # type, marked as managed by that extension. A key may not also be in `fields`.
    extension_fields: Dict[str, List[str]] = {}


class EndpointTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None
    # Replaces the whole mapping when sent. When omitted, the stored roles stay, except those
    # whose field `fields` removes.
    mapping: Optional[Dict[str, str]] = None
    # Same shape and rules as on create; a listed key must not exist on the type yet. Saved in
    # the same transaction as the other changes of this request.
    extension_fields: Optional[Dict[str, List[str]]] = None


class EndpointTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    mapping: Dict[str, str]
    # {field_key: extension name} for fields managed by an extension. Read-only.
    managed_by: Dict[str, str] = {}
    created_at: datetime
    updated_at: datetime


class EndpointCreate(BaseModel):
    type_id: str
    endpoint_data: Dict[str, Any]


class EndpointUpdate(BaseModel):
    type_id: Optional[str] = None
    endpoint_data: Dict[str, Optional[Any]] = {}


class EndpointResponse(BaseModel):
    endpoint_id: str
    type_id: str
    type_label: str
    type_description: Optional[str]
    endpoint_data: Dict[str, ResolvedField]
    created_at: datetime
    updated_at: datetime
