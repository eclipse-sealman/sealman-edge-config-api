from pydantic import BaseModel, model_validator
from typing import Optional, Dict, Any, Union
from datetime import datetime
from routers.schemas import ResolvedField, FieldDefinition, FieldDefinitionUpdate

# Not constrained to a fixed set of values - the frontend maintains an extensible registry of
# browser kinds (see registerBrowser()), so new kinds can be added there without a backend change.
BrowserKind = str

# Every service type automatically gets a built-in field at this key (integer, required,
# non-changeable) mapped to the "port" role - see db/sqlalchemy/service.py. Its structural
# properties (type/required/changeable) are fixed and enforced server-side regardless of what a
# client sends, but label/description/default/validation/ui stay admin-editable - in particular
# `default` is what drives auto-discovery (see post_network_overview.py's default-port lookup),
# so admins need a way to set it per type.
PORT_FIELD_KEY = "port"


class ServiceTypeCreate(BaseModel):
    label: str
    description: Optional[str] = None
    fields: Dict[str, FieldDefinition] = {}
    browser_kind: Optional[BrowserKind] = None

    @model_validator(mode="after")
    def _check_reserved_field(self) -> "ServiceTypeCreate":
        port_field = self.fields.get(PORT_FIELD_KEY)
        if port_field is not None and port_field.type != "integer":
            raise ValueError(f"field '{PORT_FIELD_KEY}' is built-in and must be type='integer'")
        return self


class ServiceTypeUpdate(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[Dict[str, Union[FieldDefinitionUpdate, None]]] = None
    # Unlike the other fields here, this is always applied as given (None clears it back to "no
    # browse action") rather than "None means leave unchanged" - there's no other way to clear it.
    browser_kind: Optional[BrowserKind] = None

    @model_validator(mode="after")
    def _check_reserved_field(self) -> "ServiceTypeUpdate":
        if self.fields is not None and PORT_FIELD_KEY in self.fields:
            port_patch = self.fields[PORT_FIELD_KEY]
            if port_patch is None:
                raise ValueError(f"field '{PORT_FIELD_KEY}' is built-in and cannot be removed")
            if port_patch.type is not None and port_patch.type != "integer":
                raise ValueError(f"field '{PORT_FIELD_KEY}' is built-in and must stay type='integer'")
        return self


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
