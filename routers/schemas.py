from pydantic import BaseModel, Field
from typing import Optional, List, Any, Literal


class FieldDefinition(BaseModel):
    type: Literal["string", "boolean", "integer", "number"] = Field(
        description="Data type of the field value"
    )
    label: str = Field(description="Human-readable label for UI display")
    description: Optional[str] = Field(default=None, description="Help text")
    required: bool = Field(default=False)
    regex: Optional[str] = Field(
        default=None, description="Validation regex, only for type=string"
    )
    ui: Optional[Literal["input", "toggle", "select", "textarea", "number"]] = Field(
        default=None, description="UI widget to render for this field"
    )
    options: Optional[List[str]] = Field(
        default=None,
        description="Allowed values, only used when ui=select",
    )
    default: Optional[Any] = Field(
        default=None, description="Default value if none is provided"
    )

    model_config = {"extra": "forbid"}  # no junk allowed


class FieldDefinitionUpdate(BaseModel):
    type: Optional[Literal["string", "boolean", "integer", "number"]] = Field(
        default=None, description="Data type of the field value"
    )
    label: Optional[str] = Field(
        default=None, description="Human-readable label for UI display"
    )
    description: Optional[str] = Field(default=None, description="Help text")
    required: Optional[bool] = Field(default=False)
    regex: Optional[str] = Field(
        default=None, description="Validation regex, only for type=string"
    )
    ui: Optional[Literal["input", "toggle", "select", "textarea", "number"]] = Field(
        default=None, description="UI widget to render for this field"
    )
    options: Optional[List[str]] = Field(
        default=None,
        description="Allowed values, only used when ui=select",
    )
    default: Optional[Any] = Field(
        default=None, description="Default value if none is provided"
    )

    model_config = {"extra": "forbid"}  # no junk allowed


class ResolvedField(BaseModel):
    value: Optional[Any] = None
    field: Optional[FieldDefinition] = None
