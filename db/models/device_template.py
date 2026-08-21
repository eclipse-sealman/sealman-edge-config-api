from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy import Text, func
from db.base import Base


class DeviceTemplateVariable(Base):
    """
    A single SEMS template variable applied to every device on creation (see create_device.py).
    One row per variable, replacing the old platform_config.device_template_config JSONB blob.
    """

    __tablename__ = "device_template_variables"

    variable_name: Mapped[str] = mapped_column(Text, primary_key=True)
    variable_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SelectedDeviceTemplate(Base):
    """
    A SEMS template name currently selected for use on new devices (see
    routers/platform_configuration). One row per selection, replacing the old
    platform_config.selected_templates JSONB list.
    """

    __tablename__ = "selected_device_templates"

    template_name: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
