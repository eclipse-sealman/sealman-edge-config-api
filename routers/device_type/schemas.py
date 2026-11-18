from pydantic import BaseModel, model_validator
from typing import Optional, Dict, Union
from datetime import datetime
from routers.schemas import FieldDefinition, FieldDefinitionUpdate, reject_list_fields


class DeviceTypeCreate(BaseModel):
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}

    @model_validator(mode="after")
    def _reject_lists(self) -> "DeviceTypeCreate":
        reject_list_fields(self.fields or {})
        return self


class DeviceTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None

    @model_validator(mode="after")
    def _reject_lists(self) -> "DeviceTypeUpdate":
        reject_list_fields(self.fields or {})
        return self


class DeviceTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    created_at: datetime
    updated_at: datetime
