from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from auth import validate_jwt
from db.repos.session_metric import SessionMetricRepository
from db.session import get_repository
from routers.base_api_router import BaseAPIRouter

# Deliberately generic naming and hidden from the OpenAPI schema.
ui_metrics = BaseAPIRouter(prefix="/ui/metrics", include_in_schema=False)

RANKING_SIZE = 5
MAX_VALUE = 1_000_000


class MetricSubmit(BaseModel):
    value: int = Field(ge=0, le=MAX_VALUE)


class MetricEntry(BaseModel):
    name: str
    value: int


class MetricSummary(BaseModel):
    personal: int
    best: Optional[MetricEntry] = None


def _user_id(auth_context: dict) -> str:
    user_id = auth_context.get("oid") or auth_context.get("sub")
    if not user_id:
        raise HTTPException(status_code=403, detail="User identifier missing from token")
    return user_id


async def _summary(user_id: str, repo: SessionMetricRepository) -> MetricSummary:
    return MetricSummary(personal=await repo.get_personal(user_id), best=await repo.get_best())


@ui_metrics.get("/session", response_model=MetricSummary)
async def get_session_metric(
    auth_context: dict = Depends(validate_jwt),
    repo: SessionMetricRepository = Depends(get_repository(SessionMetricRepository)),
):
    return await _summary(_user_id(auth_context), repo)


@ui_metrics.put("/session", response_model=MetricSummary)
async def put_session_metric(
    body: MetricSubmit,
    auth_context: dict = Depends(validate_jwt),
    repo: SessionMetricRepository = Depends(get_repository(SessionMetricRepository)),
):
    user_id = _user_id(auth_context)
    await repo.submit(user_id, body.value)
    return await _summary(user_id, repo)


@ui_metrics.get("/ranking", response_model=List[MetricEntry])
async def get_session_ranking(
    _: dict = Depends(validate_jwt),
    repo: SessionMetricRepository = Depends(get_repository(SessionMetricRepository)),
):
    return await repo.top(RANKING_SIZE)
