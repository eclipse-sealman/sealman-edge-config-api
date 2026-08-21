from typing import Optional, Tuple
from abc import ABC, abstractmethod


class NetworkRangeRepository(ABC):
    @abstractmethod
    async def get_range(self, device_id: str) -> Optional[Tuple[str, int]]:
        """Returns (network_definition, subnet_mask) for the device, or None if never set."""
        ...

    @abstractmethod
    async def set_range(self, device_id: str, network_definition: str, subnet_mask: int) -> None:
        """Upserts the stored range for the device."""
        ...
