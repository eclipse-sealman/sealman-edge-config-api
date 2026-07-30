from pydantic import BaseModel
from typing import Dict, List


# ---------- Templates ----------

class TemplateInfo(BaseModel):
    id: int
    name: str
    selected: bool


class TemplateListResponse(BaseModel):
    templates: List[TemplateInfo]


class SelectedTemplatesRequest(BaseModel):
    templates: List[str]


# ---------- Device Template Variables ----------

class TemplateVariableListResponse(BaseModel):
    variables: Dict[str, str]


class SetTemplateVariableRequest(BaseModel):
    value: str
