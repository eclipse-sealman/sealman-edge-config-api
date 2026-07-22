from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from exceptions import APIError
from routers.auth.routes.role import get_role_by_id, put_role_by_id
from routers.auth.schemas import RoleUpdateRequest


@pytest.mark.asyncio
async def test_get_role_by_id_returns_role_with_teams():
    role_id = uuid4()
    team_id = uuid4()
    scope_id = uuid4()
    role_repo = AsyncMock()

    role_repo.get.return_value = {
        "id": role_id,
        "name": "Reader",
        "description": "Read-only role",
        "actions": ["platform.authorization.read"],
    }
    role_repo.list_teams.return_value = [
        {
            "id": team_id,
            "name": "Ops Team",
            "scope_id": scope_id,
        }
    ]

    result = await get_role_by_id(role_id, role_repo)

    assert result == {
        "id": role_id,
        "name": "Reader",
        "description": "Read-only role",
        "actions": ["platform.authorization.read"],
        "teams": [
            {
                "id": team_id,
                "name": "Ops Team",
                "scope_id": scope_id,
            }
        ],
    }
    role_repo.get.assert_awaited_once_with(role_id)
    role_repo.list_teams.assert_awaited_once_with(role_id)


@pytest.mark.asyncio
async def test_get_role_by_id_raises_when_missing():
    role_id = uuid4()
    role_repo = AsyncMock()
    role_repo.get.return_value = None

    with pytest.raises(APIError, match=f"Role '{role_id}' was not found"):
        await get_role_by_id(role_id, role_repo)

    role_repo.list_teams.assert_not_awaited()


@pytest.mark.asyncio
async def test_put_role_by_id_updates_role_and_actions():
    role_id = uuid4()
    role_repo = AsyncMock()
    action_repo = AsyncMock()
    body = RoleUpdateRequest(
        name="Operator",
        description="Updated",
        actions=["device.read", "device.line.write"],
    )

    role_repo.get.return_value = {
        "id": role_id,
        "name": "Operator",
        "description": "Old",
        "actions": ["device.read", "device.network.discover"],
    }
    role_repo.get_by_name.return_value = None
    action_repo.get_by_names.return_value = [
        {"name": "device.read"},
        {"name": "device.line.write"},
    ]
    role_repo.update_role.return_value = {
        "id": role_id,
        "name": "Operator",
        "description": "Updated",
        "actions": ["device.line.write", "device.read"],
    }

    result = await put_role_by_id(role_id, body, role_repo, action_repo)

    assert result["id"] == role_id
    role_repo.update_role.assert_awaited_once_with(
        role_id=role_id,
        name="Operator",
        description="Updated",
        action_names=["device.read", "device.line.write"],
    )


@pytest.mark.asyncio
async def test_put_role_by_id_rejects_duplicate_action_names():
    role_id = uuid4()
    role_repo = AsyncMock()
    action_repo = AsyncMock()
    body = RoleUpdateRequest(
        name="Operator",
        description="Updated",
        actions=["device.read", "device.read"],
    )
    role_repo.get.return_value = {"id": role_id, "name": "Operator", "description": None, "actions": []}
    role_repo.get_by_name.return_value = None

    with pytest.raises(APIError, match="Duplicate action names in request"):
        await put_role_by_id(role_id, body, role_repo, action_repo)

    action_repo.get_by_names.assert_not_awaited()
    role_repo.update_role.assert_not_awaited()
