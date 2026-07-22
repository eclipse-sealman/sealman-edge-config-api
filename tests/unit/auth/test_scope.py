from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from db.models.scope import AccessRule
from exceptions import APIError
from routers.auth.routes.scope import get_scope_details, update_scope
from routers.auth.schemas import ScopeUpdateRequest


@pytest.mark.asyncio
async def test_get_scope_details_returns_scope_with_teams():
    scope_id = uuid4()
    team_id = uuid4()
    scope_repo = AsyncMock()
    scope_repo.get.return_value = {
        "id": scope_id,
        "name": "Operations",
        "description": "Ops scope",
        "attr": {"region": "eu"},
        "access_rule": AccessRule.ALL.value,
        "team_usage_count": 1,
    }
    scope_repo.list_teams.return_value = [
        {
            "id": team_id,
            "name": "Platform Team",
            "scope_id": scope_id,
        }
    ]

    result = await get_scope_details(scope_id, scope_repo)

    assert result.model_dump() == {
        "id": scope_id,
        "name": "Operations",
        "description": "Ops scope",
        "attr": {"region": "eu"},
        "access_rule": "ALL",
        "teams": [
            {
                "id": team_id,
                "name": "Platform Team",
                "scope_id": scope_id,
            }
        ],
    }
    scope_repo.get.assert_awaited_once_with(scope_id)
    scope_repo.list_teams.assert_awaited_once_with(scope_id)


@pytest.mark.asyncio
async def test_get_scope_details_raises_when_missing():
    scope_repo = AsyncMock()
    scope_repo.get.return_value = None
    scope_id = uuid4()

    with pytest.raises(APIError, match=f"Scope '{scope_id}' was not found"):
        await get_scope_details(scope_id, scope_repo)

    scope_repo.list_teams.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_scope_raises_400_for_empty_name():
    scope_repo = AsyncMock()
    scope_id = uuid4()
    request = ScopeUpdateRequest(
        name="   ",
        description="desc",
        attr={"region": "eu"},
        access_rule="ALL",
    )

    with pytest.raises(APIError) as ex:
        await update_scope(scope_id, request, scope_repo)

    assert ex.value.status_code == 400
    assert str(ex.value) == "Field 'name' must not be empty"
    scope_repo.get.assert_not_awaited()
    scope_repo.update.assert_not_awaited()