import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db.base import Base


class Extension(Base):
    """A registered API extension (an external micro-service and/or IoT Edge
    module) contributed to the platform without touching the core API.

    ``upstreams`` is a JSON map of upstream-key -> {type: "http"|"iotedge",
    base_url|module_name: ...}, mirroring the extension-controller concept from
    ec-api-postgres-example.
    """

    __tablename__ = "extensions"

    name = Column(Text, primary_key=True)
    upstreams = Column(JSONB, nullable=False, default=dict)
    description = Column(Text, nullable=False, default="")
    internal_key_hash = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class ExtensionRoute(Base):
    """A single route contributed by an extension, enough information to build
    a live FastAPI route (path/query params, body schema) and to dispatch the
    call to the resolved upstream (http proxy or IoT Hub direct method / twin).
    """

    __tablename__ = "extension_routes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extension_name = Column(Text, ForeignKey("extensions.name", ondelete="CASCADE"), nullable=False, index=True)
    upstream = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    method = Column(Text, nullable=False, default="GET")

    # http transport
    upstream_path = Column(Text, nullable=True)

    # iotedge transport
    method_name = Column(Text, nullable=True)
    iotedge_operation = Column(Text, nullable=False, default="direct_method")

    required_action = Column(Text, ForeignKey("actions.name"), nullable=True)
    visibility = Column(Text, nullable=False, default="public")  # public | internal | device

    scoped = Column(Boolean, nullable=False, default=False)
    scope_param = Column(Text, nullable=False, default="device_id")
    scope_in = Column(Text, nullable=False, default="query")  # path | query

    query_params = Column(JSONB, nullable=False, default=list)
    body_schema = Column(JSONB, nullable=True)

    summary = Column(Text, nullable=True)
    description = Column(Text, nullable=True)


class ExtensionAction(Base):
    """Provenance: records which extension introduced which RBAC action, so
    deregistration can cleanly strip the action from every role and remove it."""

    __tablename__ = "extension_actions"

    action = Column(Text, ForeignKey("actions.name", ondelete="CASCADE"), primary_key=True)
    extension_name = Column(Text, ForeignKey("extensions.name", ondelete="CASCADE"), primary_key=True)


class ExtensionDeviceKey(Base):
    """A per-device key for an extension's 'device' (field-ingress) routes,
    issued at deployment and shipped to the IoT Edge device. Namespace-locked
    to the owning extension."""

    __tablename__ = "extension_device_keys"

    extension_name = Column(Text, ForeignKey("extensions.name", ondelete="CASCADE"), primary_key=True)
    device_id = Column(Text, ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True)
    module_id = Column(Text, nullable=True)
    key_hash = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
