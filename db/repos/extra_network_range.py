from typing import List, Tuple
from abc import ABC, abstractmethod


class ExtraNetworkRangeRepository(ABC):
    @abstractmethod
    async def list_ranges(self, device_id: str) -> List[Tuple[str, int]]:
        """Returns [(network_definition, subnet_mask), ...] for the device, in creation order."""
        ...

    @abstractmethod
    async def replace_ranges(self, device_id: str, ranges: List[Tuple[str, int]]) -> None:
        """Replaces the full list of extra ranges for the device."""
        ...
