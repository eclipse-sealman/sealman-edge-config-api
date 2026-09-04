"""Wires the dynamic API extension system into the running application:
creates the 'internal' (service-to-service) and 'field' (device-ingress) side
apps - each authorized by its own API key instead of a user JWT - mounts the
'/extensions' management router on the public app, and re-hydrates every
persisted route at startup.

Unlike the reference implementation this was ported from, the two side apps
are served as asyncio tasks on the *same* event loop as the public app (via
``uvicorn.Server(...).serve()``) instead of each in their own thread with a
separate event loop - this matches how this API already runs its other
background work (see ``main.py``'s ``lifespan()``) and avoids the added
complexity of a second threading model.
"""
import asyncio
import logging
from typing import Optional

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Extension as ExtensionPermission
from constants import (
    EXTENSIONS_FIELD_API_HOST,
    EXTENSIONS_FIELD_API_PORT,
    EXTENSIONS_INTERNAL_API_HOST,
    EXTENSIONS_INTERNAL_API_PORT,
    VERSION,
)
from db.repos.extension import ExtensionRepository
from db.repos.role import RoleRepository
from db.session import get_repository
from exceptions import APIError

from . import health, registry, runtime
from .schemas import DeviceKeyRequest, ExtensionRegistration

logger = logging.getLogger("EdgeConfigAPI")


def _register_side_app_exception_handlers(side_app: FastAPI) -> None:
    """The public app's handlers (main.py) are registered on that FastAPI
    instance only - the internal/field apps need the same JSON error shape."""

    @side_app.exception_handler(APIError)
    async def _handle_api_error(_: Request, ex: APIError):
        logger.error(f"APIError: [{ex}]")
        return JSONResponse(status_code=ex.status_code, content={"message": f"{ex.message}"})

    @side_app.exception_handler(HTTPException)
    async def _handle_http_exception(_: Request, ex: HTTPException):
        logger.error(f"HTTPException: [{ex}]")
        return JSONResponse(status_code=ex.status_code, content={"message": f"{ex.detail}"})

    @side_app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, ex: RequestValidationError):
        logger.error(f"RequestValidationError: [{ex}]")
        return JSONResponse(status_code=400, content={"message": ex.body})

    @side_app.exception_handler(Exception)
    async def _handle_generic_error(_: Request, ex: Exception):
        logger.error(f"Unhandled exception on extension side-app: [{ex}]")
        return JSONResponse(status_code=500, content={"message": str(ex)})


def _create_side_apps() -> tuple[FastAPI, FastAPI]:
    """The 'internal' and 'field' apps carry no global JWT dependency - each
    route is authorized individually via X-Internal-Key / X-Device-Key."""
    internal_app = FastAPI(
        title="Edge Configuration API - Internal Extensions",
        description="Service-to-service extension routes, authorized via X-Internal-Key.",
        version=VERSION,
    )
    field_app = FastAPI(
        title="Edge Configuration API - Field Ingress Extensions",
        description="Device -> micro-service extension routes, authorized via X-Device-Key.",
        version=VERSION,
    )
    _register_side_app_exception_handlers(internal_app)
    _register_side_app_exception_handlers(field_app)
    return internal_app, field_app


def _build_management_router(public_app: FastAPI, internal_app: FastAPI, field_app: FastAPI) -> APIRouter:
    """The '/extensions' management API, mounted on the public app and
    protected by the same RBAC/ABAC dependency as every other router."""
    router = APIRouter(prefix="/extensions", tags=["Extensions"])
    manage = Depends(ABACPermissionCheck(ExtensionPermission.REGISTER, device_path=None))
    unmanage = Depends(ABACPermissionCheck(ExtensionPermission.DEREGISTER, device_path=None))

    @router.get("", dependencies=[manage])
    async def list_extensions(extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository))):
        return await extension_repo.list_extensions()

    @router.get("/{name}", dependencies=[manage])
    async def get_extension(
        name: str, extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository))
    ):
        ext = await extension_repo.get_extension(name)
        if ext is None:
            raise HTTPException(status_code=404, detail=f"Extension '{name}' not found")
        ext["routes"] = await extension_repo.list_routes(name)
        return ext

    @router.get("/{name}/health", dependencies=[manage])
    async def get_extension_health(
        name: str,
        device_id: Optional[str] = None,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ):
        """Aggregate health/version status of the extension's upstreams."""
        return await health.extension_health(extension_repo, name, device_id)

    @router.post("", status_code=201, dependencies=[manage])
    async def register_extension(
        payload: ExtensionRegistration,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
        role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
    ):
        try:
            result = await registry.register_extension(extension_repo, role_repo, payload)
        except ValueError as ex:
            raise HTTPException(status_code=409, detail=str(ex))

        runtime.add_routes_from_specs(
            public_app,
            internal_app,
            field_app,
            payload.name,
            result["routes"],
            {k: v.model_dump() for k, v in payload.upstreams.items()},
        )
        return result

    @router.delete("/{name}", dependencies=[unmanage])
    async def deregister_extension(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
        role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
    ):
        try:
            result = await registry.deregister_extension(extension_repo, role_repo, name)
        except ValueError as ex:
            raise HTTPException(status_code=404, detail=str(ex))

        for side_app in (public_app, internal_app, field_app):
            runtime.remove_live_routes(side_app, name)
        return result

    @router.post("/{name}/device-keys", dependencies=[manage])
    async def issue_device_key(
        name: str,
        payload: DeviceKeyRequest,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ):
        raw_key = await registry.issue_device_key(extension_repo, name, payload.device_id, payload.module_id)
        return {"device_id": payload.device_id, "device_key": raw_key}

    @router.get("/{name}/device-keys", dependencies=[manage])
    async def list_device_keys(
        name: str, extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository))
    ):
        return await extension_repo.list_device_keys(name)

    @router.delete("/{name}/device-keys/{device_id}", dependencies=[unmanage])
    async def revoke_device_key(
        name: str,
        device_id: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ):
        revoked = await extension_repo.revoke_device_key(name, device_id)
        if not revoked:
            raise HTTPException(status_code=404, detail=f"No device key for '{device_id}' on '{name}'")
        return {"revoked": True}

    return router


def setup_extensions(public_app: FastAPI) -> tuple[FastAPI, FastAPI]:
    """Create the internal/field side apps and mount the '/extensions'
    management router on the public app. Safe to call during app startup
    (inside ``lifespan``, before the app starts accepting requests)."""
    internal_app, field_app = _create_side_apps()
    public_app.include_router(_build_management_router(public_app, internal_app, field_app))
    return internal_app, field_app


async def hydrate_all_routes(public_app: FastAPI, internal_app: FastAPI, field_app: FastAPI) -> None:
    """Re-mount every persisted extension route. Call once at app startup so
    registrations survive a process restart."""
    from db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        extension_repo = get_repository(ExtensionRepository)(session)
        await runtime.load_all_routes(public_app, internal_app, field_app, extension_repo)


def start_side_servers(internal_app: FastAPI, field_app: FastAPI) -> list[asyncio.Task]:
    """Start the internal/field apps as asyncio tasks on the current event
    loop. Returns the created tasks so the caller can cancel them at shutdown
    (alongside this API's other background tasks)."""
    tasks = []
    for app_instance, host, port, label in (
        (internal_app, EXTENSIONS_INTERNAL_API_HOST, EXTENSIONS_INTERNAL_API_PORT, "internal"),
        (field_app, EXTENSIONS_FIELD_API_HOST, EXTENSIONS_FIELD_API_PORT, "field-ingress"),
    ):
        config = uvicorn.Config(app_instance, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        # The public app's own uvicorn process already owns process-level
        # signal handling; skip installing it again for these nested servers.
        server.install_signal_handlers = lambda: None
        tasks.append(asyncio.create_task(server.serve()))
        logger.info(f"Extension {label} API starting on {host}:{port}")
    return tasks
