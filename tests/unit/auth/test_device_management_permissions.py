import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers.auth.router import auth, get_device_management_permissions


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

    device_repo = AsyncMock()
    device_repo.get_devices_metadata.return_value = []
    device_repo.get_all_devices_raw.return_value = []
    result = asyncio.run(get_device_management_permissions({"sub": "user-123"}, user_repo, device_repo))

    assert result.can_create == bool(expected)
    assert result.deletable_device_ids == []
    user_repo.get_teams_with_roles_and_scopes.assert_awaited_once_with("user-123")


@pytest.mark.parametrize("auth_context,user_data", [({}, {"is_admin": True}), ({"sub": "unknown"}, None)])
def test_missing_identity_or_user_is_denied(auth_context, user_data):
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = user_data

    with pytest.raises(HTTPException) as error:
        asyncio.run(get_device_management_permissions(auth_context, user_repo, AsyncMock()))

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_returns_only_readable_and_deletable_devices_using_correct_metadata():
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = {
        "teams": [
            {"scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}, "roles": [{"allowed_actions": ["device.read"]}]},
            {"scope": {"attr": {"owner": "team"}, "access_rule": "ALL"}, "roles": [{"allowed_actions": ["device.delete"]}]},
        ],
    }
    device_repo = AsyncMock()
    device_repo.get_devices_metadata.return_value = [
        {"device_id": "allowed", "device_metadata": {"region": {"value": "EU"}}},
        {"device_id": "not-readable", "device_metadata": {"region": {"value": "US"}}},
        {"device_id": "default-only", "device_metadata": {"region": {"value": "EU"}, "owner": {"value": "team"}}},
    ]
    device_repo.get_all_devices_raw.return_value = [
        {"device_id": "allowed", "device_data": {"owner": "team"}},
        {"device_id": "not-readable", "device_data": {"owner": "team"}},
        {"device_id": "default-only", "device_data": {}},
    ]

    result = await get_device_management_permissions(
        auth_context={"sub": "user-1"}, user_repo=user_repo, device_repo=device_repo,
    )

    assert result.deletable_device_ids == ["allowed"]
    assert result.can_create is False
    user_repo.get_teams_with_roles_and_scopes.assert_awaited_once_with("user-1")
    device_repo.get_device_meta_raw.assert_not_called()


@pytest.mark.asyncio
async def test_create_permission_is_returned_when_no_devices_exist():
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = {
        "teams": [{"roles": [{"allowed_actions": ["device.create"]}]}],
    }
    device_repo = AsyncMock()

    result = await get_device_management_permissions(
        {"sub": "user-1"}, user_repo, device_repo,
    )

    assert result.can_create is True
    assert result.deletable_device_ids == []
    device_repo.get_all_devices_raw.assert_not_called()


@pytest.mark.asyncio
async def test_returns_only_devices_with_read_and_delete_permission():
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = {
        "is_admin": False,
        "teams": [{"scope": None, "roles": [{"allowed_actions": ["device.read"]}]}, {
            "scope": {"attr": {"region": "EU"}, "access_rule": "ALL"},
            "roles": [{"allowed_actions": ["device.delete"]}],
        }],
    }
    device_repo = AsyncMock()
    device_repo.get_all_devices_raw.return_value = [
        {"device_id": "allowed", "device_data": {"region": "EU"}},
        {"device_id": "denied", "device_data": {"region": "US"}},
        {"device_id": "not-requested", "device_data": {"region": "EU"}},
    ]
    device_repo.get_devices_metadata.return_value = [
        {"device_id": device_id, "device_metadata": {}}
        for device_id in ["allowed", "denied", "not-requested"]
    ]

    result = await get_device_management_permissions(
        {"sub": "user-1"}, user_repo, device_repo,
    )

    assert result.deletable_device_ids == ["allowed", "not-requested"]
    assert result.can_create is False
    user_repo.get_teams_with_roles_and_scopes.assert_awaited_once_with("user-1")
    device_repo.get_all_devices_raw.assert_awaited_once()
    device_repo.get_device_meta_raw.assert_not_called()


@pytest.mark.asyncio
async def test_create_and_scoped_delete_permissions_are_independent():
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = {
        "teams": [
            {"scope": None, "roles": [{"allowed_actions": ["device.read"]}]},
            {"scope": {"attr": {"region": "US"}, "access_rule": "ALL"}, "roles": [{"allowed_actions": ["device.create"]}]},
            {"scope": {"attr": {"region": "EU"}, "access_rule": "ALL"}, "roles": [{"allowed_actions": ["device.delete"]}]},
        ],
    }
    device_repo = AsyncMock()
    device_repo.get_all_devices_raw.return_value = [
        {"device_id": "eu", "device_data": {"region": "EU"}},
        {"device_id": "us", "device_data": {"region": "US"}},
    ]
    device_repo.get_devices_metadata.return_value = [
        {"device_id": "eu", "device_metadata": {}},
        {"device_id": "us", "device_metadata": {}},
    ]

    result = await get_device_management_permissions(
        {"sub": "user-1"}, user_repo, device_repo,
    )

    assert result.can_create is True
    assert result.deletable_device_ids == ["eu"]
    user_repo.get_teams_with_roles_and_scopes.assert_awaited_once_with("user-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("user_data", [
    {"is_admin": False, "teams": []},
    {"teams": [{"roles": [{"allowed_actions": ["device.delete"]}]}]},
    {"teams": [{"roles": [{"allowed_actions": ["device.read"]}]}]},
])
async def test_missing_read_or_delete_grant_does_not_read_devices(user_data):
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = user_data
    device_repo = AsyncMock()

    result = await get_device_management_permissions(
        {"sub": "user-1"}, user_repo, device_repo,
    )

    assert result.deletable_device_ids == []
    device_repo.get_all_devices_raw.assert_not_called()


@pytest.mark.asyncio
async def test_admin_can_delete_existing_devices():
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": True, "teams": []}
    device_repo = AsyncMock()
    device_repo.get_all_devices_raw.return_value = [
        {"device_id": "device-1", "device_data": {}},
        {"device_id": "device-2", "device_data": {}},
    ]
    device_repo.get_devices_metadata.return_value = [
        {"device_id": "device-1", "device_metadata": {}},
        {"device_id": "device-2", "device_metadata": {}},
    ]

    result = await get_device_management_permissions(
        {"sub": "user-1"}, user_repo, device_repo,
    )

    assert result.deletable_device_ids == ["device-1", "device-2"]
    assert result.can_create is True


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_context,user_data", [
    ({}, {"is_admin": True, "teams": []}),
    ({"sub": "unknown"}, None),
])
async def test_missing_identity_or_user_is_denied_with_device_evaluation(auth_context, user_data):
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = user_data
    device_repo = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await get_device_management_permissions(auth_context, user_repo, device_repo)

    assert error.value.status_code == 403
    device_repo.get_all_devices_raw.assert_not_called()


def test_device_management_endpoint_http_contract():
    user_repo = AsyncMock()
    user_repo.get_teams_with_roles_and_scopes.return_value = {"is_admin": True, "teams": []}
    device_repo = AsyncMock()
    device_repo.get_all_devices_raw.return_value = [{"device_id": "device-1", "device_data": {}}]
    device_repo.get_devices_metadata.return_value = [{"device_id": "device-1", "device_metadata": {}}]
    app = FastAPI()
    app.include_router(auth)
    route = next(route for route in app.routes if route.path == "/auth/permissions/device-management")
    overrides = {"auth_context": {"sub": "user-1"}, "user_repo": user_repo, "device_repo": device_repo}
    for dependency in route.dependant.dependencies:
        value = overrides[dependency.name]
        app.dependency_overrides[dependency.call] = lambda value=value: value

    with TestClient(app) as client:
        response = client.get("/auth/permissions/device-management")
        invalid_response = client.post("/auth/permissions/device-management", json={"device_ids": []})

    assert response.status_code == 200
    assert response.json() == {"can_create": True, "deletable_device_ids": ["device-1"]}
    assert invalid_response.status_code == 405