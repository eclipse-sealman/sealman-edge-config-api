from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Tuple


class HostStatusRepository(ABC):
    @abstractmethod
    async def get_host_statuses(self, device_id: str) -> Dict[str, Tuple[str, datetime]]:
        """Returns {ip: (status, changed_at)} for every host ever observed for this device."""
        ...

    @abstractmethod
    async def get_port_statuses(self, device_id: str) -> Dict[Tuple[str, int], Tuple[str, datetime]]:
        """Returns {(ip, port): (status, changed_at)} for every port ever observed for this device."""
        ...

    @abstractmethod
    async def set_host_statuses(self, device_id: str, statuses: Dict[str, Tuple[str, datetime]]) -> None:
        """Upserts {ip: (status, changed_at)} for the device."""
        ...

    @abstractmethod
    async def set_port_statuses(self, device_id: str, statuses: Dict[Tuple[str, int], Tuple[str, datetime]]) -> None:
        """Upserts {(ip, port): (status, changed_at)} for the device."""
        ...
