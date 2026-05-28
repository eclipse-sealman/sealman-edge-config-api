from pydantic import BaseModel
from typing import Optional, Dict, Any, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate


class EndpointTypeCreate(BaseModel):
    type_id: str
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}


class EndpointTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None


class EndpointTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    created_at: datetime
    updated_at: datetime


class EndpointCreate(BaseModel):
    type_id: str
    endpoint_data: Dict[str, Any]


class EndpointUpdate(BaseModel):
    endpoint_data: Dict[str, Optional[Any]]


class EndpointResponse(BaseModel):
    endpoint_id: str
    type_id: str
    type_label: str
    type_description: Optional[str]
    endpoint_data: Dict[str, ResolvedField]
    created_at: datetime
    updated_at: datetime
