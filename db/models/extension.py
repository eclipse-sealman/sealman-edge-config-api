import uuid

from sqlalchemy import Boolean, Column, ForeignKey, ForeignKeyConstraint, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db.base import Base


class Extension(Base):
    """A registered API extension (an external micro-service and/or IoT Edge
    module) contributed to the platform without touching the core API.
    """

    __tablename__ = "extensions"

    name = Column(Text, primary_key=True)
    description = Column(Text, nullable=False, default="")
    internal_key_hash = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class ExtensionUpstream(Base):
    """A transport target bundled by an extension.

    * ``http``    - a REST micro-service reached at ``base_url``.
    * ``iotedge`` - an Azure IoT Edge module (``module_name``) reached through
      the IoT Hub direct-method / module-twin REST API.
    """

    __tablename__ = "extension_upstreams"

    extension_name = Column(Text, ForeignKey("extensions.name", ondelete="CASCADE"), primary_key=True)
    key = Column(Text, primary_key=True)
    type = Column(Text, nullable=False)  # http | iotedge
    base_url = Column(Text, nullable=True)
    module_name = Column(Text, nullable=True)
    expected_version = Column(Text, nullable=True)
    health_path = Column(Text, nullable=True)
    version_field = Column(Text, nullable=True)
    version_source = Column(Text, nullable=True)
    twin_version_property = Column(Text, nullable=True)
    health_method = Column(Text, nullable=True)


class ExtensionRoute(Base):
    """A single route contributed by an extension, enough information to build
    a live FastAPI route (path/query params, body schema) and to dispatch the
    call to the resolved upstream (http proxy or IoT Hub direct method / twin).
    """

    __tablename__ = "extension_routes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["extension_name", "upstream_name"],
            ["extension_upstreams.extension_name", "extension_upstreams.key"],
            ondelete="CASCADE",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extension_name = Column(Text, ForeignKey("extensions.name", ondelete="CASCADE"), nullable=False, index=True)
    upstream_name = Column(Text, nullable=False)
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

    @property
    def upstream(self) -> str:
        return self.upstream_name

    @upstream.setter
    def upstream(self, value: str) -> None:
        self.upstream_name = value


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

