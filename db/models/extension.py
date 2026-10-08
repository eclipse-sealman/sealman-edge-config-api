"""SQLAlchemy ORM models for the dynamic API extension system.

Persistence shape only; manifest validation lives in extensions/schemas.py,
business rules (RBAC enrollment, key issuance, lifecycle) in extensions/registry.py.
"""

import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Text, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db.base import Base


class Extension(Base):
    __tablename__ = "extensions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text, nullable=False, default="")
    schema_version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    internal_key_hash = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExtensionUpstream(Base):
    """One upstream (http micro-service or iotedge module) contributed by an extension.

    Health-check configuration is split into nullable columns per upstream type.
    Check (the `last_*` columns are read/written once health checks are implemented).
    """

    __tablename__ = "extension_upstreams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extension_id = Column(UUID(as_uuid=True), ForeignKey("extensions.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(Text, nullable=False)  # the manifest's `upstreams` dict key, e.g. "svc"
    type = Column(Text, nullable=False)  # "http" | "iotedge"

    # http upstreams
    base_url = Column(Text, nullable=True)
    health_path = Column(Text, nullable=True, default="/health")
    version_field = Column(Text, nullable=True, default="version")
    expected_version = Column(Text, nullable=True)

    # iotedge upstreams
    module_name = Column(Text, nullable=True)
    # IoT Hub device query/target-condition string (same syntax as deployment targeting)
    # resolved to a canary device dynamically at check time (once health checks are implemented).
    health_device_query = Column(Text, nullable=True)

    # check-result columns, updated by whatever triggers a check (once implemented)
    # Persisted rather than ephemeral so independent callers (admin datatable, future
    # ops dashboard) share one last-known value instead of each caching their own; no
    # expiry column, staleness will be derived from last_checked_at's age by the consumer.
    last_checked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_status = Column(Text, nullable=False, default="unknown")  # unknown | healthy | unhealthy
    last_detail = Column(Text, nullable=True)


class ExtensionRoute(Base):
    """A single route contributed by an extension. Enough to mount a live FastAPI route,
    build its dynamic per-manifest `inspect.Signature` (extensions/signature.py), and
    dispatch it to its upstream (extensions/upstreams/).
    """

    __tablename__ = "extension_routes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extension_id = Column(UUID(as_uuid=True), ForeignKey("extensions.id", ondelete="CASCADE"), nullable=False, index=True)
    upstream_id = Column(UUID(as_uuid=True), ForeignKey("extension_upstreams.id", ondelete="CASCADE"), nullable=False)

    path = Column(Text, nullable=False)
    method = Column(Text, nullable=False, default="GET")
    visibility = Column(Text, nullable=False, default="public")  # public | internal

    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(JSONB, nullable=True)
    deprecated = Column(Boolean, nullable=False, default=False)
    status_code = Column(Integer, nullable=False, default=200)

    query_params = Column(JSONB, nullable=False, default=list)
    body = Column(JSONB, nullable=True)  # `declared`: author-supplied schema; `upstream_declared`/`unreachable_ref`: last-fetched snapshot, display-only
    example = Column(JSONB, nullable=True)
    validation_mode = Column(Text, nullable=True)  # declared | upstream_declared | unreachable_ref | none
    body_ref = Column(Boolean, nullable=False, default=False)  # True: body is fetched from the upstream's own OpenAPI doc, not authored here

    required_action = Column(Text, ForeignKey("actions.name"), nullable=True)
    scoped = Column(Boolean, nullable=False, default=False)
    scope_param = Column(Text, nullable=False, default="device_id")
    scope_in = Column(Text, nullable=False, default="query")

    # http transport
    upstream_path = Column(Text, nullable=True)

    # iotedge transport
    iotedge_operation = Column(Text, nullable=True)  # direct_method | twin_read | twin_write
    method_name = Column(Text, nullable=True)


class ExtensionAction(Base):
    """Provenance: which extension introduced which RBAC action, so deregistration can
    clean up actions no longer referenced by any extension."""

    __tablename__ = "extension_actions"
    __table_args__ = (
        UniqueConstraint("action_name", name="uq_extension_actions_action_name"),
    )

    action_name = Column(Text, ForeignKey("actions.name", ondelete="CASCADE"), primary_key=True)
    extension_id = Column(UUID(as_uuid=True), ForeignKey("extensions.id", ondelete="CASCADE"), primary_key=True)
