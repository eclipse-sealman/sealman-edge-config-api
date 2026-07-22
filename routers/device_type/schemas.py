from pydantic import BaseModel
from typing import Optional, Dict, Union
from datetime import datetime
from routers.schemas import FieldDefinition, FieldDefinitionUpdate


class DeviceTypeCreate(BaseModel):
    type_id: str
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}


class DeviceTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None


class DeviceTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    created_at: datetime
    updated_at: datetime
