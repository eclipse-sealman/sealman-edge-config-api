from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Any, Literal, Dict, Mapping, Set
import re

from field_validation import validate_value

FieldType = Literal["string", "boolean", "integer", "number", "list"]
# Types a list's sub-fields may have (no nested lists).
SCALAR_FIELD_TYPES: tuple[str, ...] = ("string", "boolean", "integer", "number")
# Sub-field key reserved for the row identifier.
RESERVED_ITEM_KEY = "id"
UIWidget = Literal[
    "input", "textarea", "password", "number", "slider", "select", "radio", "toggle", "checkbox"
]

# Which widgets make sense for which data type.
_UI_WIDGETS_BY_TYPE: Dict[str, Set[UIWidget]] = {
    "string": {"input", "textarea", "password", "select", "radio"},
    "boolean": {"toggle", "checkbox"},
    "integer": {"number", "slider"},
    "number": {"number", "slider"},
}
# Widget used when none is specified, so `ui` is always well-defined for a frontend to key off of.
_DEFAULT_UI_BY_TYPE: Dict[str, UIWidget] = {
    "string": "input",
    "boolean": "toggle",
    "integer": "number",
    "number": "number",
}
_OPTIONS_WIDGETS: Set[UIWidget] = {"select", "radio"}


class FieldValidation(BaseModel):
    """Constraints on a field's value. Which attributes apply depends on the field's `type`."""

    pattern: Optional[str] = Field(
        default=None, description="Regex the value must fully match (type=string only)"
    )
    min_length: Optional[int] = Field(
        default=None, ge=0, description="Minimum string length (type=string only)"
    )
    max_length: Optional[int] = Field(
        default=None, ge=0, description="Maximum string length (type=string only)"
    )
    minimum: Optional[float] = Field(
        default=None, description="Lower bound, inclusive unless exclusive_minimum is set (type=integer/number only)"
    )
    maximum: Optional[float] = Field(
        default=None, description="Upper bound, inclusive unless exclusive_maximum is set (type=integer/number only)"
    )
    exclusive_minimum: bool = Field(
        default=False, description="If true, value must be strictly greater than minimum"
    )
    exclusive_maximum: bool = Field(
        default=False, description="If true, value must be strictly less than maximum"
    )
    multiple_of: Optional[float] = Field(
        default=None, gt=0, description="Value must be a multiple of this (type=integer/number only)"
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_bounds(self) -> "FieldValidation":
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"Invalid regex pattern: {exc}")
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise ValueError("min_length cannot be greater than max_length")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum cannot be greater than maximum")
        return self


class FieldDefinition(BaseModel):
    type: FieldType = Field(description="Data type of the field value")
    label: str = Field(min_length=1, description="Human-readable label for UI display")
    description: Optional[str] = Field(default=None, description="Help text")
    required: bool = Field(default=False)
    validation: Optional[FieldValidation] = Field(
        default=None, description="Constraints on the value; allowed attributes depend on `type`"
    )
    ui: Optional[UIWidget] = Field(
        default=None, description="UI widget to render for this field; defaulted from `type` if omitted"
    )
    options: Optional[List[str]] = Field(
        default=None,
        min_length=1,
        description="Allowed values; required when ui is 'select' or 'radio' (type=string only)",
    )
    default: Optional[Any] = Field(
        default=None, description="Default value used when none is provided"
    )
    changeable: bool = Field(
        default=True,
        description="If false, the value can no longer be edited once an instance has it set",
    )
    show_in_list: bool = Field(
        default=True,
        description="If true, this field's value shows as extra info in the endpoints list",
    )
    item_fields: Optional[Dict[str, "FieldDefinition"]] = Field(
        default=None,
        description="Sub-field definitions of one row; required for type=list, not allowed otherwise",
    )
    item_order: Optional[List[str]] = Field(
        default=None,
        description="Display order of the sub-fields (type=list, response only, never accepted)",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_consistency(self) -> "FieldDefinition":
        if self.type == "list":
            return self._check_list()
        if self.item_fields is not None or self.item_order is not None:
            raise ValueError("item_fields and item_order are only supported for type='list'")

        allowed_ui = _UI_WIDGETS_BY_TYPE[self.type]
        if self.ui is None:
            self.ui = _DEFAULT_UI_BY_TYPE[self.type]
        elif self.ui not in allowed_ui:
            raise ValueError(
                f"ui='{self.ui}' is not valid for type='{self.type}' (allowed: {sorted(allowed_ui)})"
            )

        if self.options is not None:
            if self.type != "string":
                raise ValueError("options is only supported for type='string'")
            if self.ui not in _OPTIONS_WIDGETS:
                raise ValueError("options requires ui to be 'select' or 'radio'")
            if len(set(self.options)) != len(self.options):
                raise ValueError("options must not contain duplicate values")
        elif self.ui in _OPTIONS_WIDGETS:
            raise ValueError(f"ui='{self.ui}' requires options to be set")

        if self.validation is not None:
            if self.type == "string":
                if (
                    self.validation.minimum is not None
                    or self.validation.maximum is not None
                    or self.validation.multiple_of is not None
                ):
                    raise ValueError("minimum/maximum/multiple_of are not valid for type='string'")
            elif self.type in ("integer", "number"):
                if (
                    self.validation.pattern is not None
                    or self.validation.min_length is not None
                    or self.validation.max_length is not None
                ):
                    raise ValueError("pattern/min_length/max_length are not valid for type='integer'/'number'")
            elif self.type == "boolean":
                raise ValueError("validation is not supported for type='boolean'")

        if self.ui == "slider":
            if self.validation is None or self.validation.minimum is None or self.validation.maximum is None:
                raise ValueError("ui='slider' requires validation.minimum and validation.maximum")

        if self.default is not None:
            try:
                validate_value("default", self.default, self.model_dump())
            except ValueError as exc:
                raise ValueError(str(exc))

        return self

    def _check_list(self) -> "FieldDefinition":
        if not self.item_fields:
            raise ValueError("type='list' requires item_fields with at least one sub-field")
        for key, sub in self.item_fields.items():
            if not key or key == RESERVED_ITEM_KEY:
                raise ValueError(f"item_fields key '{key}' is empty or reserved")
            if sub.type not in SCALAR_FIELD_TYPES:
                raise ValueError(f"item_fields['{key}'] must be one of {list(SCALAR_FIELD_TYPES)}")
        if self.validation is not None or self.options is not None or self.default is not None or self.ui is not None:
            raise ValueError("validation, options, default and ui are not supported for type='list'")
        if not self.changeable:
            raise ValueError("changeable=false is not supported for type='list'")
        return self


def reject_list_fields(fields: Mapping[str, Any]) -> None:
    """Raise unless `fields` is free of list fields (lists exist only on endpoint types)."""
    for key, field in fields.items():
        if field is None:
            continue
        if getattr(field, "type", None) == "list" or getattr(field, "item_fields", None) is not None:
            raise ValueError(f"field '{key}': list fields are only supported on endpoint types")


class FieldDefinitionUpdate(BaseModel):
    type: Optional[FieldType] = Field(default=None, description="Data type of the field value")
    label: Optional[str] = Field(
        default=None, min_length=1, description="Human-readable label for UI display"
    )
    description: Optional[str] = Field(default=None, description="Help text")
    required: Optional[bool] = Field(default=None)
    validation: Optional[FieldValidation] = Field(
        default=None, description="Constraints on the value; allowed attributes depend on `type`"
    )
    ui: Optional[UIWidget] = Field(
        default=None, description="UI widget to render for this field"
    )
    options: Optional[List[str]] = Field(
        default=None,
        min_length=1,
        description="Allowed values; required when ui is 'select' or 'radio' (type=string only)",
    )
    default: Optional[Any] = Field(
        default=None, description="Default value used when none is provided"
    )
    changeable: Optional[bool] = Field(
        default=None,
        description="If false, the value can no longer be edited once an instance has it set",
    )
    show_in_list: Optional[bool] = Field(
        default=None,
        description="If true, this field's value shows as extra info in the endpoints list",
    )
    item_fields: Optional[Dict[str, Optional[FieldDefinition]]] = Field(
        default=None,
        description="Sub-field changes of a list field; a null value removes that sub-field",
    )

    model_config = {"extra": "forbid"}


def dump_field_patch(field: Optional[FieldDefinitionUpdate]) -> Optional[Dict[str, Any]]:
    """`model_dump(exclude_none=True)`, except that `item_fields` sub-keys sent as null stay None."""
    if field is None:
        return None
    dumped = field.model_dump(exclude_none=True)
    if field.item_fields is not None:
        dumped["item_fields"] = {
            key: sub.model_dump(exclude_none=True) if sub is not None else None
            for key, sub in field.item_fields.items()
        }
    return dumped


class ResolvedField(BaseModel):
    value: Optional[Any] = None
    field: Optional[FieldDefinition] = None
