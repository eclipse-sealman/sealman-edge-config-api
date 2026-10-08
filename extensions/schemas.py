"""Pydantic models for an extension's registration manifest and the /extensions API shapes.

Input validation shapes only. Field-level cross-references (e.g. a route's `upstream` key 
must exist in `upstreams`, `iotedge` must be present iff the referenced upstream is `type: "iotedge"`,
`body_ref` requiring an `http` upstream) are manifest-wide business rules validated by
registry.py, not enforced here.
"""

import keyword
import re
from datetime import datetime
from typing import Annotated, Dict, List, Literal, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from constants import EXTENSIONS_HTTP_UPSTREAM_ALLOWLIST

_PLACEHOLDER_RE = re.compile(r"{([^{}]*)}")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _http_upstream_is_allowed(hostname: str, port: int) -> bool:
    normalized_hostname = hostname.lower().rstrip(".")
    for entry in EXTENSIONS_HTTP_UPSTREAM_ALLOWLIST.split(","):
        entry = entry.strip()
        if not entry:
            continue
        allowed_host, separator, allowed_port = entry.rpartition(":")
        allowed_host = allowed_host.strip().strip("[]").lower().rstrip(".")
        if not separator or not allowed_host or allowed_port not in {"*", str(port)}:
            continue
        if normalized_hostname == allowed_host:
            return True
    return False


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionSpec(ManifestModel):
    name: str
    description: str = ""


class UpstreamHealthStatus(BaseModel):
    """Server-computed by POST /extensions/{name}/health-check and persisted, so every
    caller reads the same last-known value. There is no expiry; `last_checked_at`'s age
    is the staleness signal. Response-only: never part of a registration manifest."""

    last_checked_at: Optional[datetime] = None
    last_status: Literal["unknown", "healthy", "unhealthy"] = "unknown"
    last_detail: Optional[str] = None


class HttpUpstreamSpec(ManifestModel):
    type: Literal["http"]
    base_url: str
    health_path: str = "/health"
    version_field: str = "version"
    expected_version: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("base_url must not contain whitespace")
        try:
            parsed = urlsplit(value)
            parsed.port
        except ValueError as exc:
            raise ValueError("base_url must be a valid HTTP URL") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query string or fragment")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if not _http_upstream_is_allowed(parsed.hostname, port):
            raise ValueError("base_url host and port are not in EXTENSIONS_HTTP_UPSTREAM_ALLOWLIST")
        return value


class IotedgeUpstreamSpec(ManifestModel):
    type: Literal["iotedge"]
    module_name: str
    # IoT Hub device query/target-condition string (same syntax as deployment
    # targeting, e.g. tags.module='foo') resolved to a canary device dynamically
    # at check time, once implemented in a later stage not a fixed device ID, so a
    # decommissioned/renamed canary doesn't permanently break health as long as a
    # replacement still matches.
    health_device_query: Optional[str] = None

    @field_validator("module_name")
    @classmethod
    def _reject_system_modules(cls, value: str) -> str:
        if value.startswith("$"):
            raise ValueError("module_name must not target IoT Edge system modules ($edgeAgent, $edgeHub, ...)")
        return value


UpstreamSpec = Annotated[Union[HttpUpstreamSpec, IotedgeUpstreamSpec], Field(discriminator="type")]


class HttpUpstreamDetail(HttpUpstreamSpec, UpstreamHealthStatus):
    pass


class IotedgeUpstreamDetail(IotedgeUpstreamSpec, UpstreamHealthStatus):
    pass


UpstreamDetail = Annotated[Union[HttpUpstreamDetail, IotedgeUpstreamDetail], Field(discriminator="type")]


class QueryParamSpec(ManifestModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_.~\-\[\]]{1,128}$")
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = True
    description: Optional[str] = None


class IotEdgeCallSpec(ManifestModel):
    operation: Literal["direct_method", "twin_read", "twin_write"] = "direct_method"
    method_name: Optional[str] = None

    @model_validator(mode="after")
    def _check_method_name(self) -> "IotEdgeCallSpec":
        if self.operation == "direct_method" and not self.method_name:
            raise ValueError("method_name is required when operation is 'direct_method'")
        if self.operation != "direct_method" and self.method_name:
            raise ValueError("method_name must be omitted unless operation is 'direct_method'")
        return self


class RouteSpec(ManifestModel):
    upstream: str
    path: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    summary: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    deprecated: bool = False
    status_code: int = Field(default=200, ge=100, le=599)
    query_params: List[QueryParamSpec] = Field(default_factory=list)
    # Literal JSON Schema (validated via `jsonschema` at registration + request time,
    # see extensions/body_validation.py). Mutually exclusive
    # with `body_ref` on a *registration* payload.
    body: Optional[dict] = None
    example: Optional[dict] = None
    # If set, this route's body schema is fetched from its own `http` upstream's
    # `{base_url}/openapi.json` instead of being declared here (`upstream_declared` /
    # `unreachable_ref` validation_mode, see `RouteDetail`).
    body_ref: bool = False
    visibility: Literal["public", "internal"] = "public"
    required_action: Optional[str] = None
    scoped: bool = False
    scope_param: str = Field(default="device_id", pattern=r"^[A-Za-z0-9_.~\-\[\]]{1,128}$")
    scope_in: Literal["path", "query"] = "query"
    upstream_path: Optional[str] = None  # required for `http` upstreams only
    iotedge: Optional[IotEdgeCallSpec] = None  # required for `iotedge` upstreams only

    @field_validator("method", mode="before")
    @classmethod
    def _normalize_method(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("path", "upstream_path")
    @classmethod
    def _validate_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("path must start with exactly one '/'")
        if "?" in value or "#" in value:
            raise ValueError("path must not contain a query string or fragment")
        for name in _PLACEHOLDER_RE.findall(value):
            if not _IDENTIFIER_RE.fullmatch(name) or keyword.iskeyword(name):
                raise ValueError(
                    f"path placeholder '{{{name}}}' must be a Python identifier without a converter"
                )
        remainder = _PLACEHOLDER_RE.sub("", value)
        if "{" in remainder or "}" in remainder:
            raise ValueError("path has unbalanced braces")
        return value


class ExtensionRegistration(ManifestModel):
    # Required, no default: a missing or unrecognized value (anything but the literal
    # `1`) is rejected outright, mirroring $edgeAgent's own
    # schemaVersion convention. Extend to Literal[1, 2] (not widen to a plain int) the
    # day a second manifest shape actually exists.
    schema_version: Literal[1]
    name: str
    description: str = ""
    upstreams: Dict[str, UpstreamSpec]
    actions: List[ActionSpec] = Field(default_factory=list)
    routes: List[RouteSpec]


class RouteDetail(RouteSpec):
    # Server-computed from `body`/`body_ref` by registry.py. `unreachable_ref` means
    # the last fetch of the upstream's OpenAPI document failed.
    validation_mode: Optional[Literal["declared", "upstream_declared", "unreachable_ref", "none"]] = None


class ExtensionDetail(ExtensionRegistration):
    upstreams: Dict[str, UpstreamDetail]
    routes: List[RouteDetail]
    enabled: bool = False


class ExtensionHealthCheckResult(BaseModel):
    name: str
    upstreams: Dict[str, UpstreamHealthStatus]


class InternalKeyRotateResponse(BaseModel):
    internal_key: str
