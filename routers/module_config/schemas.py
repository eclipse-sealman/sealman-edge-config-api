from pydantic import BaseModel, RootModel
from typing import Any, Optional, List, Dict, Literal

class ModuleList(BaseModel):
    modules: List[str]

class ConfigStatus(BaseModel):
    desiredConfId: str | None = None
    reportedConfId: str | None = None
    confStatus: Literal["OK", "NO_CONFIG", "PENDING", "INITIAL_PENDING"]
    appStatus: Literal["OK", "ERROR", "NO_STATUS"]
    appMessage: str | None = None
    
class ModuleTwin(RootModel[Dict[Any, Any]]):
    pass
class GetModuleTwinResponse(RootModel[Optional[ModuleTwin]]):
    pass
class GetModuleTwinIdentityResponse(RootModel[Optional[ModuleTwin]]):
    pass

class ModuleConfStatus(RootModel[Dict[str, ConfigStatus]]):
    pass
