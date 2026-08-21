from typing import List
from abc import ABC, abstractmethod


class ScanPortsRepository(ABC):
    @abstractmethod
    async def get_device_ports(self, device_id: str) -> List[int]:
        """Returns every extra port persisted for this device, added via the Scan Network
        dialog."""
        ...

    @abstractmethod
    async def add_device_ports(self, device_id: str, ports: List[int]) -> None:
        """Upserts (adds if missing, no-op if already present) the given ports for this device."""
        ...

    @abstractmethod
    async def get_default_ports(self) -> List[int]:
        """Returns the global list of ports scanned on every device at minimum."""
        ...

    @abstractmethod
    async def add_default_port(self, port: int) -> None:
        """Adds a port to the global default list. No-op if already present."""
        ...

    @abstractmethod
    async def remove_default_port(self, port: int) -> None:
        """Removes a port from the global default list. No-op if not present."""
        ...
