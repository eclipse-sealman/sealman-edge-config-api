from typing import Any, Dict, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field, RootModel


class UserPermissions(BaseModel):
  Permissions: List[str] = []


class RoleCreateRequest(BaseModel):
    name: str
    description: str | None = None
    actions: List[str] = []


class RoleUpdateRequest(BaseModel):
    name: str
    description: str | None = None


class RoleActionsRequest(BaseModel):
    names: List[str]


class RoleResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    actions: List[str] = []


class ActionResponse(BaseModel):
    name: str
    description: str | None = None
    is_global: bool | None = None



class ScopeResponse(BaseModel):
  id: UUID
  name: str
  description: str | None = None
  attr: Dict[str, Any]
  access_rule: Literal["ALL", "ANY"]
  team_usage_count: int


class TeamSummaryResponse(BaseModel):
  id: UUID
  name: str
  scope_id: UUID | None = None


class ScopeDetailsResponse(BaseModel):
  id: UUID
  name: str
  description: str | None = None
  attr: Dict[str, Any]
  access_rule: Literal["ALL", "ANY"]
  teams: List[TeamSummaryResponse] = []


class UserSummaryResponse(BaseModel):
  id: str
  preferred_username: str
  is_admin: bool
  is_new: bool


class TeamDetailsResponse(TeamSummaryResponse):
  scope: ScopeResponse | None = None
  roles: List[RoleResponse] = []
  users: List[UserSummaryResponse] = []


class UserWithTeamsResponse(UserSummaryResponse):
  teams: List[TeamSummaryResponse] = []


class TeamCreateRequest(BaseModel):
  name: str
  scope_id: UUID | None = None
  user_ids: List[str] = []
  role_ids: List[UUID] = []


class TeamUpdateRequest(BaseModel):
  name: str
  scope_id: UUID | None = None


class TeamAddUserRequest(BaseModel):
  user_id: str


class TeamAddRoleRequest(BaseModel):
  role_id: UUID


class ScopeCreateRequest(BaseModel):
  name: str = Field(min_length=1)
  description: str | None = None
  attr: Dict[str, Any]
  access_rule: Literal["ALL", "ANY"]


class ScopeUpdateRequest(BaseModel):
  name: str
  description: str | None = None
  attr: Dict[str, Any]
  access_rule: Literal["ALL", "ANY"]


class TeamListResponse(RootModel[List[TeamSummaryResponse]]):
  pass


class UserListResponse(RootModel[List[UserWithTeamsResponse]]):
  pass


class ScopeListResponse(RootModel[List[ScopeResponse]]):
  pass
