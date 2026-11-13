from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PlatformFunctionSettings:
    network_scan_enabled: bool = True


class PlatformFunctionsRepository(ABC):
    @abstractmethod
    async def get_functions(self) -> PlatformFunctionSettings:
        """Returns the saved settings, or the defaults if none were saved yet."""
        ...

    @abstractmethod
    async def save_functions(self, settings: PlatformFunctionSettings) -> PlatformFunctionSettings:
        ...
