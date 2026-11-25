from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PasswordRenewalTaskInformation(BaseModel):
    taskId: str
    scheduledTime: datetime
    status: Literal["Pending", "Canceled", "Completed", "Error"]
    errorCount: int
    latestError: str | None = None
    createdAt: datetime
    updatedAt: datetime


class DeviceSecretInformation(BaseModel):
    deviceTypeHasAuthSecret: bool
    forceRenewal: bool = False
    secretValueRenewAfterDays: int = 0
    error: str | None = None
    id: int | None = None
    secretCreatedAt: datetime | None = None
    secretUpdatedAt: datetime | None = None
    renewalTask: PasswordRenewalTaskInformation | None = None


class DeviceSecretValue(BaseModel):
    id: int
    secretValue: str
    renewalTask: PasswordRenewalTaskInformation | None = None


class PasswordRenewalAccepted(BaseModel):
    requestAccepted: Literal[True] = True
