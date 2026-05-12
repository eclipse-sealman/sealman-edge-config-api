import asyncio
import logging
from typing import Set
from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from auth import validate_jwt, SWAGGER_CLIENT_ID, refresh_jwks_cache, fetch_oidc_issuer
from contextlib import asynccontextmanager
from constants import (
    DEVICE_CACHE_INTERVAL,
    IOT_HUB_NAME,
    CORS_ALLOWED_ORIGINS,
    VERSION,
    ROOT_PATH,
    ALLOW_STARTUP_WITHOUT_OIDC,
    BOOTSTRAP_ENABLED,
)

from exceptions import APIError
from db.migration import run_migrations
from db.postgres import AsyncSessionLocal, create_device_repository
from periodic_task import create_periodic_task
from routers.devices.routes.get_devices import populate_cache_from_iot_hub_query
from routers.smart_ems.password_renewal_task_processor import process_password_renewal_tasks
from smart_ems import init_smart_ems
from bootstrap import bootstrap_sems, bootstrap_iothub_base_deployment
from helper import AuditTrail

from routers.cmd_proxy.router import cmd_proxy
from routers.general.router import general
from routers.module_config.router import module_config
from routers.smart_ems.router import smart_ems
from routers.network_discovery.router import network_discovery
from routers.auth.router import auth
from routers.lines.router import lines
from routers.platform_configuration.router import platform_config
from routers.devices.router import devices

# logger config
logger = logging.getLogger("EdgeConfigAPI")
logger.setLevel(logging.INFO)
log_handler = logging.StreamHandler()
log_formatter = logging.Formatter(fmt="%(levelname)s:     %(asctime)s >> %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

logger.info(f"Edge-Config-API ({VERSION})")
background_tasks: Set[asyncio.Task] = set()

async def populate_cache_from_iot_hub_query_wrapper():
    async with AsyncSessionLocal() as session:
        repo = create_device_repository(session)
        await populate_cache_from_iot_hub_query(repo)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_migrations)

    jwks_refresh_task = None

    try:
        await fetch_oidc_issuer()
        jwks_refresh_task = asyncio.create_task(refresh_jwks_cache())
    except Exception as ex:
        if not ALLOW_STARTUP_WITHOUT_OIDC:
            raise

        logger.warning(
            "OIDC discovery unavailable during startup. "
            "Continuing because ALLOW_STARTUP_WITHOUT_OIDC=true. "
            f"Authentication-protected requests may fail until the provider is reachable: {ex}"
        )

    if BOOTSTRAP_ENABLED:
        await bootstrap_sems()
        await bootstrap_iothub_base_deployment()

    sems_token_refresh = asyncio.create_task(init_smart_ems())

    background_tasks.add(asyncio.create_task(create_periodic_task(
        func=populate_cache_from_iot_hub_query_wrapper,
        interval=DEVICE_CACHE_INTERVAL,
    )))

    background_tasks.add(asyncio.create_task(create_periodic_task(
        func=process_password_renewal_tasks,
        interval=(30 * 60),  # every 30 minutes,
        initial_delay=60  # initial delay of 1 minute
    )))
    
    yield
    
    if jwks_refresh_task is not None:
        jwks_refresh_task.cancel()
    sems_token_refresh.cancel()

    for task in background_tasks:
        task.cancel()
    
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)


# init api -> run in terminal: 'uvicorn main:app'
app = FastAPI(
    title="Edge Configuration API",
    description=f"API for configuring Edge Device Modules on IoTHub ({IOT_HUB_NAME}).",
    version=VERSION,
    root_path=ROOT_PATH,
    lifespan=lifespan,
    dependencies=[Security(validate_jwt)],  # Every request must provide a token signed for this application
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    swagger_ui_oauth2_redirect_url='/docs/oauth2-redirect',
    swagger_ui_init_oauth={
        'usePkceWithAuthorizationCodeGrant': True,
        'clientId': SWAGGER_CLIENT_ID,
    },
)

allowed_origins=[]
if CORS_ALLOWED_ORIGINS is not None:
    allowed_origins.extend(CORS_ALLOWED_ORIGINS.split(","))

# add cors middleware to api
app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)


# add http middleware to log the called route path to audit-trail
@app.middleware("http")
async def add_path_to_audit_trail_log(request: Request, call_next):
    query_params = ""
    if len(request.query_params) != 0:
        query_params = f"?{request.query_params}"
    await AuditTrail.log_route(f"{request.method} {request.url.path}{query_params}")
    response = await call_next(request)
    return response

# mount statics
app.mount("/static", StaticFiles(directory="static"), name="static")

# attach default routers
app.include_router(auth)
app.include_router(general)
app.include_router(module_config)
app.include_router(smart_ems)
app.include_router(cmd_proxy)
app.include_router(network_discovery)
app.include_router(lines)
app.include_router(platform_config)
app.include_router(devices)


# Register docs routes as plain Starlette routes so they bypass the global JWT dependency
async def _swagger_ui(req: Request):
    root = ROOT_PATH or ""
    return get_swagger_ui_html(
        openapi_url=f"{root}/openapi.json",
        title="",
        oauth2_redirect_url=f"{root}/docs/oauth2-redirect",
        swagger_favicon_url="/static/api-logo.png",
        init_oauth=app.swagger_ui_init_oauth,
        swagger_ui_parameters=app.swagger_ui_parameters,
    )

async def _swagger_redirect(req: Request):
    return get_swagger_ui_oauth2_redirect_html()

app.add_route("/docs", _swagger_ui, include_in_schema=False)
app.add_route("/docs/oauth2-redirect", _swagger_redirect, include_in_schema=False)


# exception handlers
@app.exception_handler(APIError)
async def handle_api_error(request: Request, ex: APIError):
    logger.error(f"APIError: [{ex}]")
    return JSONResponse(
        status_code=ex.status_code,
        content={"message": f"{ex.message}"},
    )


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, ex: HTTPException):
    logger.error(f"HTTPException: [{ex}]")
    return JSONResponse(
        status_code=ex.status_code,
        content={"message": f"{ex.detail}"}
    )


@app.exception_handler(RequestValidationError)
async def handle_fast_api_req_validation_error(request: Request, ex: RequestValidationError):
    logger.error(f"RequestValidationError: [{ex}]")
    return JSONResponse(
        status_code=400,
        content={"message": ex.body}
    )


@app.exception_handler(Exception)
async def handle(request: Request, ex):
    logger.error(f"Unhandled exception: [{ex}]")
    return JSONResponse(
        status_code=500,
        content={"message": str(ex)}
    )


# startup with uvicorn if the script executed correctly
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=int(5000))
