from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_VALUE_LENGTH = 100
MAX_FILTERS = 20
MAX_COLUMN_VALUES = 100

# device_id: the id equals the value · field: a metadata field equals the value ·
# metadata / endpoint / service: any metadata / endpoint / service value contains the value
# (an endpoint / service filter may name `field` to look at that one field only)
SearchFilterType = Literal["device_id", "field", "metadata", "endpoint", "service"]
FIELD_FILTER_TYPES = ("field", "endpoint", "service")


class DeviceSearchFilter(BaseModel):
    type: SearchFilterType
    field: Optional[str] = Field(default=None, min_length=1, max_length=MAX_VALUE_LENGTH)
    value: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)

    @field_validator("value")
    @classmethod
    def _value_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @model_validator(mode="after")
    def _field_belongs_to_field_filters(self):
        if self.type == "field" and self.field is None:
            raise ValueError("field is required for a 'field' filter")
        if self.type not in FIELD_FILTER_TYPES and self.field is not None:
            raise ValueError("field is only allowed for 'field', 'endpoint' and 'service' filters")
        return self


class DeviceColumnFilter(BaseModel):
    """The metadata field equals ANY of the values."""

    field: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)
    values: List[str] = Field(min_length=1, max_length=MAX_COLUMN_VALUES)


class DeviceSearchRequest(BaseModel):
    """A device matches when it satisfies ALL filters AND ALL column filters."""

    filters: List[DeviceSearchFilter] = Field(default_factory=list, max_length=MAX_FILTERS)
    column_filters: List[DeviceColumnFilter] = Field(default_factory=list, max_length=MAX_FILTERS)


class DeviceSearchMatchField(BaseModel):
    key: Optional[str] = None
    label: str
    value: str


class DeviceSearchMatch(BaseModel):
    """What matched on a device. `endpoint_*` and `service_id` are set for endpoint and service
    matches (a service match also names the endpoint it belongs to)."""

    kind: Literal["device_id", "metadata", "endpoint", "service"]
    fields: List[DeviceSearchMatchField]
    endpoint_id: Optional[str] = None
    endpoint_name: Optional[str] = None
    type_label: Optional[str] = None
    service_id: Optional[str] = None


class DeviceSearchResult(BaseModel):
    device_id: str
    matches: List[DeviceSearchMatch]


class DeviceSearchResponse(BaseModel):
    total: int
    items: List[DeviceSearchResult]


class DeviceSearchValuesResponse(BaseModel):
    values: List[str]
    # more values exist than were returned (the column-value list is capped)
    has_more: bool = False


class DeviceSearchKey(BaseModel):
    key: str
    label: str


class DeviceSearchKeysResponse(BaseModel):
    keys: List[DeviceSearchKey]
    # more keys match than were returned
    has_more: bool = False
