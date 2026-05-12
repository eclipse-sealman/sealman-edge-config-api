from typing import List
from pydantic import BaseModel


class UserPermissions(BaseModel):
  ResourceType: str
  ResourceId: str
  Permissions: List[str] = []
