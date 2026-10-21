from typing import Dict, List, Literal

from pydantic import BaseModel

from routers.schemas import FieldDefinition


class ExtensionFieldSet(BaseModel):
    extension: str
    enabled: bool
    fields: Dict[str, FieldDefinition]
    # Every declared key, mapped to the sorted type_ids it is applied to ([] if none).
    applied: Dict[str, List[str]]


class ApplyExtensionFieldsRequest(BaseModel):
    # No min_length: empty lists are rejected in code with 422 (a Pydantic error would be 400).
    type_ids: List[str]
    keys: List[str]


class AppliedField(BaseModel):
    type_id: str
    key: str


class SkippedField(BaseModel):
    type_id: str
    key: str
    reason: Literal["key_exists"]


class ApplyExtensionFieldsResponse(BaseModel):
    applied: List[AppliedField]
    skipped: List[SkippedField]
