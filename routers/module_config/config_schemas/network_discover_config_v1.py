from ipaddress import IPv4Address
from pydantic import BaseModel, Field, StringConstraints, RootModel
from typing import Annotated, Dict, Set, Optional

# Cron pattern regex from JSON Schema
SCHEDULED_CRON_PATTERN = r"^(\*|(?:[0-9]|(?:[1-5][0-9]))(?:(?:\-[0-9]|\-(?:[1-5][0-9]))?|(?:\,(?:[0-9]|(?:[1-5][0-9])))*)) (\*|(?:[0-9]|1[0-9]|2[0-3])(?:(?:\-(?:[0-9]|1[0-9]|2[0-3]))?|(?:\,(?:[0-9]|1[0-9]|2[0-3]))*)) (\*|(?:[1-9]|(?:[12][0-9])|3[01])(?:(?:\-(?:[1-9]|(?:[12][0-9])|3[01]))?|(?:\,(?:[1-9]|(?:[12][0-9])|3[01]))*)) (\*|(?:[1-9]|1[012]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:(?:\-(?:[1-9]|1[012]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))?|(?:\,(?:[1-9]|1[012]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))*)) (\*|(?:[0-6]|sun|mon|tue|wed|thu|fri|sat)(?:(?:\-(?:[0-6]|sun|mon|tue|wed|thu|fri|sat))?|(?:\,(?:[0-6]|sun|mon|tue|wed|thu|fri|sat))*))$"

class ScanDefinition(BaseModel):
    networkDefinition: IPv4Address
    subnetMask: Annotated[int, Field(..., ge=0, le=32)]
    ports: Set[int]

class Endpoint(BaseModel):
    name: str
    description: str | None = None
    # Port numbers as keys, service names as values
    serviceNames: Dict[int, str]

class NetworkDiscoverModuleConfigV1(BaseModel):
    scheduledCron: Annotated[str, StringConstraints(pattern=SCHEDULED_CRON_PATTERN)]
    scanDefinition: ScanDefinition
    endpointNames: Dict[IPv4Address, Endpoint]

class GetNetDiscoverModuleConfigV1(RootModel[Optional[NetworkDiscoverModuleConfigV1]]):
    pass