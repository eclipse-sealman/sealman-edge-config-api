"""Wiring for the extension system: the internal side app and the /extensions
management router.

Business logic lives in `extensions/registry.py`; this module only builds the side
app, mounts the router (with real `db/repos`-backed dependencies, not a module-level
stub), and re-hydrates persisted routes at startup.
"""

import asyncio
import logging
from collections import defaultdict
from typing import DefaultDict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Extension
from constants import (
    EXTENSIONS_ENABLED,
    EXTENSIONS_INTERNAL_API_HOST,
    EXTENSIONS_INTERNAL_API_PORT,
)
from db.repos.extension import ExtensionRepository
from db.repos.role import RoleRepository
from db.session import AsyncSessionLocal, get_repository
from exceptions import APIError
from routers.base_api_router import BaseAPIRouter

from . import registry, runtime
from .schemas import (
    ExtensionDetail,
    ExtensionHealthCheckResult,
    ExtensionRegistration,
    InternalKeyRotateResponse,
)

# Built once at import time: model_json_schema() is a pure function of the model
# definition, not per-request state.
_REGISTRATION_SCHEMA = ExtensionRegistration.model_json_schema()

logger = logging.getLogger("EdgeConfigAPI")

# Separate ASGI apps (not app.mount()) so each can run its own uvicorn.Server on its own
# port/network. The bind address is deployment-configurable; see constants.py and the
# README's "Extension system side apps" section for the network-isolation requirement.
internal_app = FastAPI(
    title="Edge Configuration API — Internal Extension Router",
    description="Service-to-service router for extension microservices, authenticated via X-Internal-Key. Never expose this port publicly.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@internal_app.get("/", include_in_schema=False)
async def _internal_liveness():
    return {"app": "internal", "status": "ok"}


def _build_management_router() -> BaseAPIRouter:
    """Builds the /extensions management router.

    Kept inline here (not under routers/<feature>/router.py) because it's constructed
    together with, and closes over, the two side apps it also mounts/unmounts routes on.
    """
    router = BaseAPIRouter(prefix="/extensions", tags=["Admin – Extensions"])
    register_dep = Depends(ABACPermissionCheck(Extension.REGISTER, device_path=None))
    deregister_dep = Depends(ABACPermissionCheck(Extension.DEREGISTER, device_path=None))
    read_dep = Depends(ABACPermissionCheck(Extension.READ, device_path=None))

    @router.post(
        "",
        summary="Register a new extension",
        description=(
            "Persists an extension's manifest with enabled=false. A freshly-registered "
            "extension is disabled by default; no routes are mounted until it is enabled."
        ),
        response_model=ExtensionDetail,
        status_code=201,
        dependencies=[register_dep],
    )
    async def register_extension(
        registration: ExtensionRegistration,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> ExtensionDetail:
        return await registry.register_extension(extension_repo, registration)

    # Registered before "/{name}". A literal "/schema" path segment must be matched
    # before the parameterized route would otherwise capture it as `name="schema"`.
    @router.get(
        "/schema",
        summary="Get the extension registration manifest's JSON Schema",
        description=(
            "Returns ExtensionRegistration.model_json_schema() verbatim, so extension "
            "authors/tooling can validate a manifest before calling POST /extensions. "
            "Stays behind extension.read, same as the other GET routes. It is not a "
            "public document: this API already requires a JWT, and the schema is an "
            "admin artifact, not something anonymous clients need."
        ),
        dependencies=[read_dep],
    )
    async def get_registration_schema() -> dict:
        return _REGISTRATION_SCHEMA

    @router.get(
        "",
        summary="List registered extensions",
        response_model=List[ExtensionDetail],
        dependencies=[read_dep],
    )
    async def list_extensions(
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> List[ExtensionDetail]:
        return await registry.list_extensions(extension_repo)

    @router.get(
        "/{name}",
        summary="Get one registered extension",
        response_model=ExtensionDetail,
        dependencies=[read_dep],
    )
    async def get_extension(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> ExtensionDetail:
        return await registry.get_extension(extension_repo, name)

    @router.put(
        "/{name}",
        summary="Replace an extension's manifest",
        description=(
            "Replaces the entire manifest in one database transaction; never changes "
            "`enabled`. If the extension is currently enabled, its routes are rebuilt and "
            "swapped in after that commit; if they cannot be built, the extension is disabled. "
            "Refuses (409) to drop an action still "
        ),
        response_model=ExtensionDetail,
        dependencies=[register_dep],
    )
    async def replace_extension(
        name: str,
        registration: ExtensionRegistration,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> ExtensionDetail:
        async with _extension_locks[name]:
            detail = await registry.replace_extension(extension_repo, name, registration)
            if not detail.enabled:
                return detail
            # New route ids only exist after the commit, so this build cannot precede it.
            try:
                built = await _build_persisted_routes(extension_repo, name)
            except Exception as exc:
                logger.exception(f"Replaced extension '{name}' could not be mounted; disabling it")
                runtime.publish_routes(_public_app, internal_app, name, None)
                await registry.disable_extension(extension_repo, name)
                raise APIError(f"Extension '{name}' was saved but could not be mounted and was disabled: {exc}", 500)
            runtime.publish_routes(_public_app, internal_app, name, built)
            return detail

    @router.delete(
        "/{name}",
        summary="Deregister an extension",
        description=(
            "Strips this extension's actions from every role that holds them, deletes the "
            "extension and leftover Action rows in one transaction, then unmounts live routes."
        ),
        status_code=204,
        dependencies=[deregister_dep],
    )
    async def delete_extension(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
        role_repo: RoleRepository = Depends(get_repository(RoleRepository)),
    ) -> None:
        async with _extension_locks[name]:
            await registry.deregister_extension(extension_repo, role_repo, name)
            runtime.publish_routes(_public_app, internal_app, name, None)

    @router.post(
        "/{name}/enable",
        summary="Enable an extension",
        description=(
            "Re-fetches every body_ref schema (the retry for unreachable_ref), then "
            "mounts every persisted route onto the correct app by visibility. Disable "
            "and enable again to retry a failed upstream OpenAPI fetch without a restart."
        ),
        response_model=ExtensionDetail,
        dependencies=[register_dep],
    )
    async def enable_extension(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> ExtensionDetail:
        async with _extension_locks[name]:
            # Refetch before mount so a previously unreachable body_ref can flip to
            # upstream_declared without a process restart. See registry.refresh_extension_ref_schemas.
            await registry.refresh_extension_ref_schemas(extension_repo, name)
            try:
                built = await _build_persisted_routes(extension_repo, name)
            except Exception as exc:
                logger.exception(f"Extension '{name}' could not be mounted; it stays disabled")
                raise APIError(f"Extension '{name}' could not be mounted: {exc}", 500)
            detail = await registry.enable_extension(extension_repo, name)
            runtime.publish_routes(_public_app, internal_app, name, built)
            return detail

    @router.post(
        "/{name}/disable",
        summary="Disable an extension",
        description="Unmounts every live route for this extension. RBAC grants and issued keys are untouched.",
        response_model=ExtensionDetail,
        dependencies=[deregister_dep],
    )
    async def disable_extension(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> ExtensionDetail:
        async with _extension_locks[name]:
            detail = await registry.disable_extension(extension_repo, name)
            runtime.publish_routes(_public_app, internal_app, name, None)
            return detail

    @router.post(
        "/{name}/health-check",
        summary="Check an extension's upstream health now",
        description=(
            "Runs a fresh health check of every one of this extension's upstreams and "
            "persists the result. A plain GET never triggers a check on its own, it "
            "only ever returns whatever was last persisted here. Gated by extension.read, "
            "not extension.register: this refreshes a status the settings page shows, "
            "it does not change the manifest."
        ),
        response_model=ExtensionHealthCheckResult,
        dependencies=[read_dep],
    )
    async def check_extension_health(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> ExtensionHealthCheckResult:
        return await registry.check_extension_health(extension_repo, name)

    @router.post(
        "/{name}/internal-key/rotate",
        summary="Rotate an extension's internal key",
        description=(
            "Issues a fresh X-Internal-Key and invalidates the previous one immediately. "
            "The raw key is only ever returned here. Never re-readable via GET."
        ),
        response_model=InternalKeyRotateResponse,
        dependencies=[register_dep],
    )
    async def rotate_internal_key(
        name: str,
        extension_repo: ExtensionRepository = Depends(get_repository(ExtensionRepository)),
    ) -> InternalKeyRotateResponse:
        raw_key = await registry.rotate_internal_key(extension_repo, name)
        return InternalKeyRotateResponse(internal_key=raw_key)

    return router


_public_app: Optional[FastAPI] = None

# Serializes lifecycle changes per extension; one process only (see README).
_extension_locks: DefaultDict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _build_persisted_routes(extension_repo: ExtensionRepository, name: str) -> runtime.BuiltRoutes:
    upstreams = await extension_repo.list_upstreams(name)
    routes = await extension_repo.list_routes(name)
    return runtime.build_routes(_public_app, internal_app, name, upstreams, routes)


def setup_extensions(app: FastAPI) -> None:
    """Mounts the static /extensions management API onto the public app."""
    global _public_app
    _public_app = app
    app.include_router(_build_management_router())


async def hydrate_all_routes() -> None:
    """Re-mounts persisted, enabled extension routes at startup, on both apps. Also
    re-fetches every `body_ref` route's upstream OpenAPI schema first (see
    registry.refresh_ref_schemas), so a route that was `unreachable_ref` when the
    process last started flips to `upstream_declared` (or vice versa) without a manual
    PUT, purely by restarting. Failures are logged; they never abort API startup."""
    if _public_app is None:
        return
    async with AsyncSessionLocal() as session:
        extension_repo = get_repository(ExtensionRepository)(session)
        try:
            await registry.refresh_ref_schemas(extension_repo)
        except Exception:
            logger.exception("Startup refresh of extension body schemas failed; mounting stored schemas")
            await session.rollback()
        try:
            await runtime.load_all_routes(_public_app, internal_app, extension_repo)
        except Exception:
            logger.exception("Could not load extension routes; the API starts without them")


def start_side_apps() -> List[asyncio.Task]:
    """Starts the internal side app as a task on the caller's running event loop."""
    if not EXTENSIONS_ENABLED:
        return []

    config = uvicorn.Config(internal_app, host=EXTENSIONS_INTERNAL_API_HOST, port=EXTENSIONS_INTERNAL_API_PORT, log_level="info")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # the public app's uvicorn already owns this

    logger.info(f"Starting extension side app: internal on {EXTENSIONS_INTERNAL_API_HOST}:{EXTENSIONS_INTERNAL_API_PORT}")
    return [asyncio.create_task(_serve_internal_app(server))]


async def _serve_internal_app(server: uvicorn.Server) -> None:
    try:
        await server.serve()
    except SystemExit:
        # uvicorn calls sys.exit() when startup fails, e.g. the port is already bound.
        logger.error(
            f"Internal extension app could not start on {EXTENSIONS_INTERNAL_API_HOST}:"
            f"{EXTENSIONS_INTERNAL_API_PORT} (port in use by another worker or a stale process?). "
            "Internal extension routes are unavailable in this process; the public API keeps running."
        )

