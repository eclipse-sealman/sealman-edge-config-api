from sqlalchemy import TIMESTAMP, Column, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from db.base import Base


class ModuleConfigTemplateGroup(Base):
    """Groups templates. Ties them to modules (exact names) and, optionally, to scopes: a device may
    use the templates only if its metadata matches one of the scopes. No scopes: available for every device."""
    __tablename__ = "module_config_template_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)
    module_names = Column(JSONB, nullable=False, default=list)
    created_by = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ModuleConfigTemplateGroupScope(Base):
    __tablename__ = "module_config_template_group_scopes"

    group_id = Column(Integer, ForeignKey("module_config_template_groups.id", ondelete="CASCADE"), primary_key=True)
    scope_id = Column(UUID(as_uuid=True), ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True)


class ModuleConfigTemplate(Base):
    __tablename__ = "module_config_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)
    # template: a module configuration; block: a reusable part referenced by {"$ref": "<id>"}
    kind = Column(Text, nullable=False, server_default="template")
    group_id = Column(Integer, ForeignKey("module_config_template_groups.id", ondelete="RESTRICT"), nullable=False)
    derived_from_template_id = Column(Integer, ForeignKey("module_config_templates.id", ondelete="SET NULL"))
    derived_from_version = Column(Integer)
    archived_at = Column(TIMESTAMP(timezone=True))
    created_by = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ModuleConfigTemplateVersion(Base):
    """Immutable: every edit of a template inserts the next version; older versions are a read-only history
    (devices always use the latest)."""
    __tablename__ = "module_config_template_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("module_config_templates.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    body = Column(JSONB, nullable=False)
    variables = Column(JSONB, nullable=False, default=list)
    slots = Column(JSONB, nullable=False, default=list, server_default="[]")
    comment = Column(Text)
    created_by = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_module_config_template_version"),
    )


class ModuleConfigRevision(Base):
    """Append-only history of every configuration written to a device module.
    The content itself lives in the immutable blob `{device}/{module}/{blob_name}`."""
    __tablename__ = "module_config_revisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Text, nullable=False)
    module_name = Column(Text, nullable=False)
    config_id = Column(Text, nullable=False)
    blob_name = Column(Text, nullable=False)
    parent_revision_id = Column(Integer, ForeignKey("module_config_revisions.id", ondelete="SET NULL"))
    # template | raw | legacy-api | extension:<name> | external | reapply
    source = Column(Text, nullable=False)
    template_id = Column(Integer, ForeignKey("module_config_templates.id", ondelete="SET NULL"))
    values = Column(JSONB)
    content_hash = Column(Text)
    actor = Column(Text)
    comment = Column(Text)
    reapplied_from_id = Column(Integer, ForeignKey("module_config_revisions.id", ondelete="SET NULL"))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("device_id", "module_name", "config_id", name="uq_module_config_revision_config_id"),
        Index("ix_module_config_revisions_device_module", "device_id", "module_name", "id"),
    )
