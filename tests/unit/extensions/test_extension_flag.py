from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

import main


def test_disabled_extensions_do_not_register_management_router(monkeypatch):
    target_app = FastAPI()
    setup_extensions = Mock()
    monkeypatch.setattr(main, "EXTENSIONS_ENABLED", False)
    monkeypatch.setattr(main, "setup_extensions", setup_extensions)

    main._register_extensions_router(target_app)

    setup_extensions.assert_not_called()
    assert not any(route.path.startswith("/extensions") for route in target_app.routes)


@pytest.mark.asyncio
async def test_disabled_extensions_do_not_hydrate_or_start_side_app(monkeypatch):
    hydrate_all_routes = AsyncMock()
    start_side_apps = Mock()
    monkeypatch.setattr(main, "EXTENSIONS_ENABLED", False)
    monkeypatch.setattr(main, "hydrate_all_routes", hydrate_all_routes)
    monkeypatch.setattr(main, "start_side_apps", start_side_apps)

    tasks = await main._start_extensions()

    assert tasks == []
    hydrate_all_routes.assert_not_awaited()
    start_side_apps.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_extensions_hydrate_and_start_side_app(monkeypatch):
    task = Mock()
    hydrate_all_routes = AsyncMock()
    start_side_apps = Mock(return_value=[task])
    monkeypatch.setattr(main, "EXTENSIONS_ENABLED", True)
    monkeypatch.setattr(main, "hydrate_all_routes", hydrate_all_routes)
    monkeypatch.setattr(main, "start_side_apps", start_side_apps)

    tasks = await main._start_extensions()

    assert tasks == [task]
    hydrate_all_routes.assert_awaited_once_with()
    start_side_apps.assert_called_once_with()