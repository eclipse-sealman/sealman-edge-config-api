from pydantic import BaseModel
from typing import Optional, Dict, Any, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate


class ServiceTypeCreate(BaseModel):
    type_id: str
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}


class ServiceTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None


class ServiceTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
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
