from datetime import datetime
import logging
import uuid
from typing import List

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.password_renewal import PasswordRenewalTask, PasswordRenewalTaskStatus
from db.registry import register_repository
from db.repos.password_renewal_task import PasswordRenewalTaskRepository


logger = logging.getLogger("EdgeConfigAPI")

@register_repository(PasswordRenewalTaskRepository)
class SqlAlchemyPasswordRenewalTaskRepository(PasswordRenewalTaskRepository):

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_pending_tasks(self, scheduled_time: datetime) -> List[PasswordRenewalTask]:
        result = await self._session.execute(
            select(PasswordRenewalTask).where(
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING,
                PasswordRenewalTask.scheduled_time < scheduled_time,
            )
        )
        return list(result.scalars().all())

    async def complete_task(self, task: PasswordRenewalTask) -> None:
        logger.info(f"Marking task {task.task_id} as completed for device {task.device_id}")
        task.status = PasswordRenewalTaskStatus.COMPLETED
        await self._session.commit()

    async def get_pending_tasks_for_device_count(self, device: str) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(PasswordRenewalTask).where(
                PasswordRenewalTask.device_id == device,
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING,
            )
        )
        return result.scalar_one()

    async def cancel_task(self, task: PasswordRenewalTask) -> None:
        logger.info(f"Cancelling task {task.task_id} for device {task.device_id}")
        task.status = PasswordRenewalTaskStatus.CANCELED
        await self._session.commit()

    async def cancel_scheduled_tasks_for_device(self, device: str) -> None:
        await self._session.execute(
            update(PasswordRenewalTask)
            .where(
                PasswordRenewalTask.device_id == device,
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING,
            )
            .values(status=PasswordRenewalTaskStatus.CANCELED)
        )
        await self._session.commit()

    async def schedule_task(self, device: str, secret_id: int, task_schedule_time: datetime) -> str:
        task_id = str(uuid.uuid4())
        task = PasswordRenewalTask(
            task_id=task_id,
            device_id=device,
            secret_id=secret_id,
            scheduled_time=task_schedule_time,
            status=PasswordRenewalTaskStatus.PENDING,
            error_count=0,
            latest_error=None,
        )
        self._session.add(task)
        await self._session.commit()
        return task_id

    async def update_task_error(self, task: PasswordRenewalTask, error_message: str) -> None:
        task.error_count += 1
        task.latest_error = error_message
        if task.error_count >= 5:
            task.status = PasswordRenewalTaskStatus.ERROR
            logger.error(f"Task {task.task_id} for device {task.device_id} has exceeded error limit.")
        await self._session.commit()
        logger.error(f"Task {task.task_id} for device {task.device_id} encountered an error: {error_message}")

    async def purge_completed_tasks(self, cutoff_date: datetime) -> int:
        result = await self._session.execute(
            delete(PasswordRenewalTask).where(
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.COMPLETED,
                PasswordRenewalTask.created_at < cutoff_date,
            )
        )
        await self._session.commit()
        deleted_count = result.rowcount or 0
        if deleted_count > 0:
            logger.debug(f"Deleted {deleted_count} completed password renewal tasks")
        return deleted_count
