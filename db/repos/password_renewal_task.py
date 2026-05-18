from abc import ABC, abstractmethod
from datetime import datetime
from typing import List

from db.models.password_renewal import PasswordRenewalTask


class PasswordRenewalTaskRepository(ABC):

    @abstractmethod
    async def get_pending_tasks(self, scheduled_time: datetime) -> List[PasswordRenewalTask]:
        pass

    @abstractmethod
    async def complete_task(self, task: PasswordRenewalTask) -> None:
        pass

    @abstractmethod
    async def get_pending_tasks_for_device_count(self, device: str) -> int:
        pass

    @abstractmethod
    async def cancel_task(self, task: PasswordRenewalTask) -> None:
        pass

    @abstractmethod
    async def cancel_scheduled_tasks_for_device(self, device: str) -> None:
        pass

    @abstractmethod
    async def schedule_task(self, device: str, secret_id: int, task_schedule_time: datetime) -> str:
        pass

    @abstractmethod
    async def update_task_error(self, task: PasswordRenewalTask, error_message: str) -> None:
        pass

    @abstractmethod
    async def purge_completed_tasks(self, cutoff_date: datetime) -> int:
        pass
