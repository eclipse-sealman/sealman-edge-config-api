import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from routers.auth.router import get_device_create_permissions


@pytest.mark.parametrize("user_data,expected", [
    ({"is_admin": True}, ["device.create"]),
    ({"teams": [{"roles": [{"allowed_actions": ["device.create"]}],
                  "scope": {"attr": "customer", "access_rule": "restricted"}}]}, ["device.create"]),
    ({"teams": [{"roles": [{"allowed_actions": ["device.read"]}]}]}, []),
    ({"teams": []}, []),
])
def test_create_eligibility(user_data, expected):
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = user_data

    result = asyncio.run(get_device_create_permissions({"sub": "user-123"}, user_repo))

    assert result.permissions == expected
    user_repo.get_teams_with_roles_and_scopes.assert_awaited_once_with("user-123")


@pytest.mark.parametrize("auth_context,user_data", [({}, {"is_admin": True}), ({"sub": "unknown"}, None)])
def test_missing_identity_or_user_is_denied(auth_context, user_data):
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = user_data

    with pytest.raises(HTTPException) as error:
        asyncio.run(get_device_create_permissions(auth_context, user_repo))

    assert error.value.status_code == 403