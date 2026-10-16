"""Request schemas for device endpoints."""

from typing import Any

from pydantic import BaseModel, Field


class DeviceMetadataRequest(BaseModel):
    """Required metadata for the default device type, plus any type-specific fields."""

    model_config = {"extra": "allow"}

    city: str = Field(description="Device city.")
    countryCode: str = Field(description="Device country code.")
    description: str = Field(description="Device description stored in project metadata.")
    geoLocation: str = Field(description="Device coordinates, typically latitude,longitude.")


class CreateDeviceRequest(BaseModel):
    """Payload for registering a device through ``PUT /devices/{device_id}``."""

    model_config = {
        # Preserve compatibility with clients that already send additional fields.
        "extra": "allow",
        "json_schema_extra": {
            "examples": [
                {
                    "meta": {
                        "city": "Kempten",
                        "countryCode": "DE",
                        "description": "Example edge device",
                        "geoLocation": "47.737315601756144,10.339716700209028",
                    },
                    "type_id": "default",
                    "registration_id_generated": "123456",
                    "description": "Example edge device",
                    "deviceType": "9",
                }
            ]
        },
    }

    authType: str | None = Field(default=None, description="Device authentication type.")
    meta: DeviceMetadataRequest = Field(
        description="Required project metadata, plus any additional fields for the selected device type."
    )
    type_id: str | None = Field(
        default=None,
        description="Project device type ID; defaults to 'default'.",
    )
    registration_id_generated: str = Field(
        default="",
        description="Smart-EMS registration ID; defaults to the device ID when omitted.",
    )
    imei: str | None = None
    model: str | None = None
    registrationId: str | None = None
    endorsementKey: str | None = Field(
        default=None,
        description="Device endorsement key, if applicable.",
        json_schema_extra={"writeOnly": True},
    )
    hardwareVersion: str | None = None
    description: str = ""
    deviceType: str | int | None = None
