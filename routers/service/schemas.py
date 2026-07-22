from pydantic import BaseModel, model_validator
from typing import Optional, Dict, Any, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate


class ServiceTypeCreate(BaseModel):
    type_id: str
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}
    mapping: Dict[str, str] = {}

    @model_validator(mode="after")
    def _check_mapping(self) -> "ServiceTypeCreate":
        unknown = set(self.mapping) - set(self.fields)
        if unknown:
            raise ValueError(f"mapping references unknown field(s): {sorted(unknown)}")
        for key, value in self.mapping.items():
            if not value or not value.strip():
                raise ValueError(f"mapping value for '{key}' must be a non-empty string")
        return self


class ServiceTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None
    mapping: Optional[Dict[str, Optional[str]]] = None


class ServiceTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    mapping: Dict[str, str]
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
