import copy
import hashlib
import logging
import re
from typing import Any, Dict, List, Tuple, Union

from fastapi import Depends, FastAPI, HTTPException, Request

from authorization.abac_permission_check import ABACPermissionCheck
from authorization.permission_types import Device

from extensions import body_validation
from routers.base_api_router import BaseAPIRouter
from routers.module_config.registry import ActiveModuleSchema, module_schema_registry
from routers.module_config.payload import ModuleConfigPayload, ModuleJSONResponse, invalid_module_payload, parse_module_json
from routers.module_config.schemas import GetModuleTwinIdentityResponse, GetModuleTwinBinaryResponse, GetModuleTwinResponse, ModuleList, ModuleConfStatus
from routers.module_config.routes.post_module_twin_config import post_module_twin_config as _post_module_twin_config
from routers.module_config.routes.get_module_twin_identity_reported import get_module_twin_identity_reported as _get_module_twin_identity_reported
from routers.module_config.routes.get_module_twin_config import get_module_twin_config as _get_module_twin_config
from routers.module_config.routes.get_module_twin_config_binary import get_module_twin_config_binary as _get_module_twin_config_binary
from routers.module_config.routes.post_module_config_status import (post_module_config_status
                                                                    as _post_module_config_status)

module_config = BaseAPIRouter()

logger = logging.getLogger("EdgeConfigAPI")

_CONFIG_OPERATION_PATH = "/{device}/twin/config/{module}"
_REPORTED_OPERATION_PATH = "/{device}/twin/identity/{module}/reported"
_MODULE_SCHEMA_UNAVAILABLE_MESSAGE = "Module schema is unavailable"
_MODULE_SCHEMA_MISMATCH_MESSAGE = "Module data could not be validated against its active schema"
_FIXED_CONFIG_PATHS = {
    "/{device}/twin/config/seal-module-opcua-client": "seal-module-opcua-client",
    "/{device}/twin/config/seal-app-net-discover": "seal-app-net-discover",
}
_FIXED_CONFIG_DESCRIPTION = (
    "Deprecated fixed-module alias of the generic configuration contract. Uses only this module's "
    "active configuration schema, or the generic fallback when inactive. An enabled extension's "
    "unavailable claim returns 503 before storage access; it never falls back."
)
_CONFIG_POST_OPENAPI_EXTRA = {"requestBody": {"required": True, "content": {
    "application/json": {"schema": {"anyOf": [{"type": "object"}]}}
}}}

# ============================================================
# POST Endpoints - Write Operations  
# ============================================================

@module_config.post("/{device}/twin/config/seal-module-opcua-client", tags=["Module Configuration"],
                    deprecated=True, description=_FIXED_CONFIG_DESCRIPTION,
                    openapi_extra=_CONFIG_POST_OPENAPI_EXTRA)
async def post_module_twin_config_opcua(device: str,
                                        request: Request,
                                        _ = Depends(ABACPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE))):
    return await _write_module_configuration(device, "seal-module-opcua-client", request)

@module_config.post("/{device}/twin/config/seal-app-net-discover", tags=["Module Configuration"],
                    deprecated=True, description=_FIXED_CONFIG_DESCRIPTION,
                    openapi_extra=_CONFIG_POST_OPENAPI_EXTRA)
async def post_module_twin_config_discover(device: str, request: Request,
                                  _ = Depends(ABACPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE))):
    return await _write_module_configuration(device, "seal-app-net-discover", request)

@module_config.post(
    _CONFIG_OPERATION_PATH,
    tags=["Module Configuration"],
    description=(
        "Validates the exact module's current configuration schema at runtime. Without an active "
        "schema, only JSON objects are accepted. Supplied JSON is persisted without normalization."
    ),
    openapi_extra=_CONFIG_POST_OPENAPI_EXTRA,
)
async def post_module_twin_config(device: str, module: str, request: Request,
                                 _ = Depends(ABACPermissionCheck(Device.MODULE_TWIN_CONFIG_WRITE))):
    return await _write_module_configuration(device, module, request)


async def _write_module_configuration(device: str, module: str, request: Request):
    _refuse_unavailable_module(module)
    raw = await body_validation.read_limited_body(request)
    # Lifecycle may have changed while awaiting the body. Select only after that await.
    _refuse_unavailable_module(module)
    configuration = module_schema_registry.get(module, "configuration")
    payload = parse_module_json(raw)
    if configuration is None:
        if not isinstance(payload, dict):
            return invalid_module_payload([{"loc": [], "rule": "type"}])
    else:
        try:
            body_validation.check_schema(configuration.schema)
            for component in configuration.schema.get("components", {}).get("schemas", {}).values():
                body_validation.check_schema(component)
            errors = body_validation.validate_module_snapshot(
                module, configuration.purpose, configuration.version, configuration.schema, payload
            )
        except Exception:
            raise HTTPException(status_code=503, detail=_MODULE_SCHEMA_UNAVAILABLE_MESSAGE)
        if errors:
            return invalid_module_payload(errors)
    request_payload = ModuleConfigPayload(payload)
    result = await _post_module_twin_config(device, module, request_payload)
    return ModuleJSONResponse(result)


@module_config.post("/{device}/config/status", response_model=ModuleConfStatus,
                    tags=["Module Configuration"])
async def post_module_config_status(device: str, module_list: ModuleList,
                                    _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _post_module_config_status(device, module_list)

# ============================================================
# GET Endpoints - Read Operations
# ============================================================

@module_config.get("/{device}/twin/config/seal-app-net-discover", response_model=Union[GetModuleTwinResponse, None],
                   tags=["Module Configuration"], deprecated=True, description=_FIXED_CONFIG_DESCRIPTION)
async def get_net_discover_twin_config(device: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _read_module_configuration(device, "seal-app-net-discover")

@module_config.get("/{device}/twin/config/seal-module-opcua-client", response_model=Union[GetModuleTwinResponse, None],
                   tags=["Module Configuration"], deprecated=True, description=_FIXED_CONFIG_DESCRIPTION)
async def get_opcua_client_twin_config(device: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _read_module_configuration(device, "seal-module-opcua-client")

def _refuse_unavailable_module(module: str) -> None:
    """A module that an enabled extension claims but cannot activate never falls back to the generic response."""
    if module_schema_registry.is_unavailable(module):
        raise HTTPException(status_code=503, detail=_MODULE_SCHEMA_UNAVAILABLE_MESSAGE)


def _ensure_matches_schema(module: str, active: ActiveModuleSchema, payload: Any) -> None:
    """Stored or reported data that violates its active snapshot is a server error. The details stay in the log."""
    try:
        errors = body_validation.validate_module_snapshot(module, active.purpose, active.version, active.schema, payload)
    except Exception:
        logger.exception(f"Module '{module}' {active.purpose} schema could not be applied")
        raise HTTPException(status_code=500, detail=_MODULE_SCHEMA_MISMATCH_MESSAGE)
    if errors:
        logger.error(f"Module '{module}' {active.purpose} data violates its active schema: {errors}")
        raise HTTPException(status_code=500, detail=_MODULE_SCHEMA_MISMATCH_MESSAGE)


@module_config.get(_CONFIG_OPERATION_PATH, response_model=Union[GetModuleTwinResponse, None],
                   tags=["Module Configuration"])
async def get_module_twin_config(device: str, module: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _read_module_configuration(device, module)


async def _read_module_configuration(device: str, module: str):
    _refuse_unavailable_module(module)
    configuration = module_schema_registry.get(module, "configuration")
    payload = await _get_module_twin_config(device, module)
    if configuration is not None and payload is not None:
        _ensure_matches_schema(module, configuration, payload)
    return payload


@module_config.get("/{device}/twin/config/{module}/binary", response_model=GetModuleTwinBinaryResponse,
                   tags=["Module Configuration"])
async def get_module_twin_config_binary(device: str, module: str,
                                        _ = Depends(ABACPermissionCheck(Device.READ))):
    return await _get_module_twin_config_binary(device, module)

@module_config.get(_REPORTED_OPERATION_PATH, response_model=GetModuleTwinIdentityResponse,
                   tags=["Module Configuration"])
async def get_module_twin_identity_reported(device: str, module: str,
                                 _ = Depends(ABACPermissionCheck(Device.READ))):
    _refuse_unavailable_module(module)
    reported = module_schema_registry.get(module, "reported")
    payload = await _get_module_twin_identity_reported(device, module)
    if reported is not None and payload is not None:
        _ensure_matches_schema(module, reported, payload)
    return payload


# ============================================================
# OpenAPI - generic module schema alternatives
# ============================================================

_COMPONENT_REF_PREFIX = "#/components/schemas/"
_COMPONENT_NAME_INVALID_RE = re.compile(r"[^A-Za-z0-9._-]")


def _component_ref(name: str) -> str:
    return f"{_COMPONENT_REF_PREFIX}{name}"


def _snapshot_alternative(active: ActiveModuleSchema) -> Tuple[dict, Dict[str, dict]]:
    """Returns one active snapshot's root schema and its components, renamed under a prefix derived
    from the exact module ID and purpose so they cannot collide with core or other module components."""
    digest = hashlib.sha256(f"{active.purpose}:{active.module_id}".encode("utf-8")).hexdigest()[:12]
    prefix = f"Module_{active.purpose}_{digest}_"

    def hoisted(name: str) -> str:
        return prefix + _COMPONENT_NAME_INVALID_RE.sub("_", name)

    def rewrite(node: Any) -> Any:
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if isinstance(node, dict):
            return {key: _rewrite_ref(value) if key == "$ref" and isinstance(value, str) else rewrite(value)
                    for key, value in node.items()}
        return node

    def _rewrite_ref(ref: str) -> str:
        if not ref.startswith(_COMPONENT_REF_PREFIX):
            return ref
        name = ref[len(_COMPONENT_REF_PREFIX):].replace("~1", "/").replace("~0", "~")
        return _component_ref(hoisted(name))

    root = rewrite({key: value for key, value in active.schema.items() if key != "components"})
    components = {hoisted(name): rewrite(component)
                  for name, component in active.schema.get("components", {}).get("schemas", {}).items()}
    return root, components


def _publish_generic_alternatives(schema: dict) -> None:
    """Publishes purpose-specific GET alternatives and POST's object fallback/configuration schemas.
    Runtime selects by exact module ID; the document describes all current alternatives."""
    operations = (
        (_CONFIG_OPERATION_PATH, "configuration", GetModuleTwinResponse),
        (_REPORTED_OPERATION_PATH, "reported", GetModuleTwinIdentityResponse),
    )
    for path, purpose, core_model in operations:
        operation = schema.get("paths", {}).get(path, {}).get("get")
        if operation is None:
            continue
        active = [entry for entry in module_schema_registry.list_active() if entry.purpose == purpose]
        if not active:
            continue
        alternatives: List[dict] = [{"$ref": _component_ref(core_model.__name__)}]
        components: Dict[str, dict] = {}
        for entry in active:
            root, nested = _snapshot_alternative(entry)
            alternatives.append(root)
            components.update(nested)
        alternatives.append({"type": "null"})

        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        operation["responses"]["200"]["content"]["application/json"]["schema"] = {
            **{key: value for key, value in response_schema.items() if key not in ("$ref", "anyOf")},
            "anyOf": alternatives,
        }
        schema.setdefault("components", {}).setdefault("schemas", {}).update(components)

    post = schema.get("paths", {}).get(_CONFIG_OPERATION_PATH, {}).get("post")
    if post is not None:
        alternatives = [{"type": "object"}]
        for entry in module_schema_registry.list_active():
            if entry.purpose == "configuration":
                root, nested = _snapshot_alternative(entry)
                alternatives.append(root)
                schema.setdefault("components", {}).setdefault("schemas", {}).update(nested)
        post["requestBody"] = {"required": True, "content": {
            "application/json": {"schema": {"anyOf": alternatives}}
        }}


def _publish_fixed_module_schemas(schema: dict) -> None:
    """Fixed aliases publish only their exact module's configuration snapshot, never the union."""
    for path, module in _FIXED_CONFIG_PATHS.items():
        operations = schema.get("paths", {}).get(path, {})
        active = module_schema_registry.get(module, "configuration")
        if active is None:
            continue  # Generated GET object/null and required POST object are the generic fallbacks.
        root, nested = _snapshot_alternative(active)
        schema.setdefault("components", {}).setdefault("schemas", {}).update(nested)
        get = operations.get("get")
        if get is not None:
            get["responses"]["200"]["content"]["application/json"]["schema"] = {
                "anyOf": [root, {"type": "null"}],
            }
        post = operations.get("post")
        if post is not None:
            post["requestBody"] = {"required": True, "content": {
                "application/json": {"schema": root}
            }}


def install_module_config_openapi(app: FastAPI) -> None:
    """Publishes active module schemas on generic and fixed configuration operations. It works on a copy of the
    generated document, so FastAPI's cached `openapi_schema` is never mutated and each read reflects
    the registry as it is now, including schemas removed by disable or replace."""
    generate = app.openapi

    def openapi() -> Dict[str, Any]:
        schema = copy.deepcopy(generate())
        _publish_generic_alternatives(schema)
        _publish_fixed_module_schemas(schema)
        return schema

    app.openapi = openapi
