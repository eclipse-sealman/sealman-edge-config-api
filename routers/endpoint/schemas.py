from pydantic import BaseModel, model_validator
from typing import Optional, Dict, Any, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate

# Every endpoint type automatically gets built-in fields at these keys - "ip" (mapped to the "ip"
# role, see db/sqlalchemy/endpoint.py) and "name" (a plain instance label, so e.g. two endpoints
# of the same type can be told apart - see EndpointResponse.endpoint_data). Their structural
# properties (type/required/changeable) are fixed and enforced server-side regardless of what a
# client sends, but label/description/default/validation/ui stay admin-editable - in particular
# `ip`'s `default` is what drives auto-discovery's "suggested IPs" (see get_network_scan_range.py).
IP_FIELD_KEY = "ip"
NAME_FIELD_KEY = "name"
_RESERVED_FIELD_TYPES = {IP_FIELD_KEY: "string", NAME_FIELD_KEY: "string"}


class EndpointTypeCreate(BaseModel):
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}

    @model_validator(mode="after")
    def _check_reserved_fields(self) -> "EndpointTypeCreate":
        for key, expected_type in _RESERVED_FIELD_TYPES.items():
            field = self.fields.get(key)
            if field is not None and field.type != expected_type:
                raise ValueError(f"field '{key}' is built-in and must be type='{expected_type}'")
        return self


class EndpointTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None

    @model_validator(mode="after")
    def _check_reserved_fields(self) -> "EndpointTypeUpdate":
        for key, expected_type in _RESERVED_FIELD_TYPES.items():
            if self.fields is not None and key in self.fields:
                patch = self.fields[key]
                if patch is None:
                    raise ValueError(f"field '{key}' is built-in and cannot be removed")
                if patch.type is not None and patch.type != expected_type:
                    raise ValueError(f"field '{key}' is built-in and must stay type='{expected_type}'")
        return self


class EndpointTypeResponse(BaseModel):
    type_id: str
    label: str
    description: Optional[str]
    fields: Dict[str, FieldDefinition]
    mapping: Dict[str, str]
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
