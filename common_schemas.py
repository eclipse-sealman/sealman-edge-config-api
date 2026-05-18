from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated, Generic, TypeVar

IPv4SubnetStr = Annotated[str, StringConstraints(pattern=r"^(?:[0-9]|[1-2][0-9]|3[0-2])$")]
IPv4SubnetInt = Annotated[int, Field(strict=True, ge=0, le=32)]


DirectMethodPayload = TypeVar('DirectMethodPayload', bound=BaseModel)


class DirectMethod(BaseModel, Generic[DirectMethodPayload]):
    status: int
    payload: DirectMethodPayload
