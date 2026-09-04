"""Pydantic models describing an extension registration payload.

Ported from the ec-api-postgres-example prototype's dynamic API extension
system, adapted to Edge Config API's existing action-naming convention
(dotted strings like ``device.read``) and device identifier (``device_id``).
"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Scalar types usable for documented path/query parameters.
ScalarType = Literal["string", "integer", "number", "boolean"]


class ActionSpec(BaseModel):
    """An RBAC action the extension introduces (e.g. ``webftp.connect``)."""

    name: str = Field(..., description="Dotted action name, e.g. 'webftp.connect'")
    description: str = ""


class UpstreamSpec(BaseModel):
    """A transport target an extension can proxy to.

    * ``http``    - a REST micro-service reached at ``base_url``.
    * ``iotedge`` - an Azure IoT Edge module (``module_name``) reached through
      the IoT Hub direct-method / module-twin REST API (the same mechanism
      already used by ``routers/cmd_proxy`` and ``routers/module_config``).
    """

    type: Literal["http", "iotedge"]
    base_url: Optional[str] = Field(
        None, description="Base URL of the micro-service (required for type 'http')."
    )
    module_name: Optional[str] = Field(
        None, description="IoT Edge module id to address (required for type 'iotedge')."
    )

    # --- health / version verification --------------------------------------
    expected_version: Optional[str] = Field(
        None,
        description="Version requirement the deployed upstream must satisfy, as a "
        "PEP 440 / semver-style specifier, e.g. '>=1.2.0,<2.0.0'.",
    )
    # http
    health_path: Optional[str] = Field(
        None, description="[http] Health endpoint path (default '/health')."
    )
    version_field: Optional[str] = Field(
        None,
        description="[http] Dotted field in the health response holding the "
        "running version (default 'version').",
    )
    # iotedge
    version_source: Optional[Literal["image_tag", "twin_reported"]] = Field(
        None,
        description="[iotedge] Where to read the deployed version: from the "
        "container image tag reported by $edgeAgent ('image_tag', default) or "
        "from a module-twin reported property ('twin_reported').",
    )
    twin_version_property: Optional[str] = Field(
        None,
        description="[iotedge] Reported-property name holding the version when "
        "version_source='twin_reported' (default 'version').",
    )
    health_method: Optional[str] = Field(
        None,
        description="[iotedge] Optional direct-method name used as a liveness "
        "probe during a health check.",
    )


class QueryParamSpec(BaseModel):
    """A query parameter that should be documented (and validated) on a route."""

    name: str
    type: ScalarType = "string"
    required: bool = True
    description: Optional[str] = None


class IotEdgeCallSpec(BaseModel):
    """Describes what an ``iotedge`` route actually does against the module.

    Required on every route whose upstream is type ``iotedge``; must be
    omitted for ``http`` routes.
    """

    operation: Literal["direct_method", "twin_read", "twin_write"] = Field(
        "direct_method",
        description="- 'direct_method' (default): invokes 'method_name' on the module "
        "via an IoT Hub direct method.\n"
        "- 'twin_read': GETs the module twin (reported + desired properties).\n"
        "- 'twin_write': merge-patches the module twin's desired properties with "
        "the route's request body.",
    )
    method_name: Optional[str] = Field(
        None,
        description="Direct-method name to invoke on the module. Required when "
        "'operation' is 'direct_method' and must be omitted otherwise.",
    )

    @model_validator(mode="after")
    def _check_method_name(self) -> "IotEdgeCallSpec":
        if self.operation == "direct_method" and not self.method_name:
            raise ValueError("iotedge.method_name is required for operation 'direct_method'")
        if self.operation != "direct_method" and self.method_name:
            raise ValueError(
                f"iotedge.method_name must be omitted for operation '{self.operation}'"
            )
        return self


class RouteSpec(BaseModel):
    upstream: str = Field(
        ..., description="Key into the extension's 'upstreams' map; selects the transport."
    )
    path: str = Field(
        ...,
        description="Public path exposed by Edge Config API. May contain path "
        "parameters, e.g. '/webftp/{device_id}/connect'.",
    )
    method: Optional[str] = Field(
        None,
        description="Public HTTP method. Defaults to 'GET' for http upstreams; for "
        "iotedge upstreams defaults to 'POST' for 'direct_method'/'twin_write' and "
        "'GET' for 'twin_read'.",
    )

    # --- http transport -----------------------------------------------------
    upstream_path: Optional[str] = Field(
        None,
        description="[http] Path on the micro-service to proxy to. May contain "
        "'{param}' placeholders filled from the public path parameters.",
    )

    # --- iotedge transport --------------------------------------------------
    iotedge: Optional[IotEdgeCallSpec] = Field(
        None,
        description="[iotedge] What this route does against the module - required "
        "for routes on an 'iotedge' upstream, must be omitted for 'http' routes.",
    )

    # --- visibility ---------------------------------------------------------
    visibility: Literal["public", "internal", "device"] = Field(
        "public",
        description="Which channel the route belongs to:\n"
        "- 'public' (default): user-facing API, RBAC + ABAC via the existing "
        "ABACPermissionCheck.\n"
        "- 'internal': internal service-to-service router (separate, non-exposed "
        "port). Authorized by the extension's 'X-Internal-Key' (namespace-locked).\n"
        "- 'device': field-ingress router for edge-module -> micro-service calls. "
        "Authorized by a per-device 'X-Device-Key'; must target an 'http' upstream.",
    )

    required_action: Optional[str] = Field(
        None, description="Action the caller must hold to invoke this route (public only)."
    )

    # --- ABAC scoping ---------------------------------------------------------
    scoped: bool = Field(
        False,
        description="If true (public only), the caller's ABAC device scope is "
        "evaluated against the referenced device before proxying (reuses "
        "authorization.abac_permission_check.ABACPermissionCheck).",
    )
    scope_param: str = Field(
        "device_id", description="Name of the parameter carrying the device identifier."
    )
    scope_in: Literal["path", "query"] = Field(
        "query", description="Where the device identifier is passed."
    )

    # --- request shaping ----------------------------------------------------
    query_params: List[QueryParamSpec] = Field(default_factory=list)
    body: Optional[dict] = Field(
        None,
        description="Declared request body as a JSON Schema object. When set, "
        "/docs shows the schema and the incoming body is validated against it.",
    )
    body_schema_ref: Optional[str] = Field(
        None,
        description="Path to a JSON Schema file relative to schema directory. "
        "Mutually exclusive with 'body'.",
    )

    summary: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _check_body_ref_exclusive(self) -> "RouteSpec":
        if self.body is not None and self.body_schema_ref:
            raise ValueError(
                "RouteSpec: 'body' and 'body_schema_ref' are mutually exclusive"
            )
        return self


class ExtensionRegistration(BaseModel):
    name: str = Field(..., description="Unique extension identifier")
    upstreams: Dict[str, UpstreamSpec] = Field(
        ..., description="Named transport targets this extension can proxy to."
    )
    description: str = ""
    actions: List[ActionSpec] = Field(default_factory=list)
    grant_to_roles: List[str] = Field(
        default_factory=list,
        description="Existing role names that should immediately receive the new actions",
    )
    routes: List[RouteSpec]


class DeviceKeyRequest(BaseModel):
    """Request body to issue a per-device key for an extension's 'device' routes."""

    device_id: str = Field(..., description="Edge Config API device id")
    module_id: Optional[str] = Field(None, description="Optional module id (informational)")
