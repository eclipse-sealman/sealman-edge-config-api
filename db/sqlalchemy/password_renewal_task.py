from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
import logging
import uuid
from typing import List

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.password_renewal import PasswordRenewalTask, PasswordRenewalTaskStatus
from db.models.device import Device
from exceptions import UnmatchedDependency
from db.registry import register_repository
from db.repos.password_renewal_task import PasswordRenewalTaskRepository


logger = logging.getLogger("EdgeConfigAPI")

@register_repository(PasswordRenewalTaskRepository)
class SqlAlchemyPasswordRenewalTaskRepository(PasswordRenewalTaskRepository):

    def __init__(self, session: AsyncSession):
        self._session = session
        self._device_locked = False

    @asynccontextmanager
    async def lock_device(self, device: str):
        if self._device_locked:
            raise RuntimeError("Nested password renewal device locks are not supported")
        try:
            result = await self._session.execute(
                select(Device.device_id).where(Device.device_id == device).with_for_update()
            )
            if result.scalar_one_or_none() is None:
                raise UnmatchedDependency("Device not found", status_code=404)
            self._device_locked = True
            yield
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        finally:
            self._device_locked = False

    async def _save(self):
        if self._device_locked:
            await self._session.flush()
        else:
            await self._session.commit()

    async def get_latest_task_for_device(self, device: str) -> PasswordRenewalTask | None:
        result = await self._session.execute(
            select(PasswordRenewalTask)
            .where(PasswordRenewalTask.device_id == device)
            .order_by(
                (PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING).desc(),
                PasswordRenewalTask.created_at.desc(),
                PasswordRenewalTask.task_id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_pending_task(
        self, task_id: str, scheduled_time: datetime
    ) -> PasswordRenewalTask | None:
        result = await self._session.execute(
            select(PasswordRenewalTask).where(
                PasswordRenewalTask.task_id == task_id,
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING,
                PasswordRenewalTask.scheduled_time <= scheduled_time,
                or_(
                    PasswordRenewalTask.next_attempt_at.is_(None),
                    PasswordRenewalTask.next_attempt_at <= scheduled_time,
                ),
            ).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_pending_tasks(self, scheduled_time: datetime) -> List[PasswordRenewalTask]:
        result = await self._session.execute(
            select(PasswordRenewalTask).where(
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING,
                PasswordRenewalTask.scheduled_time <= scheduled_time,
                or_(
                    PasswordRenewalTask.next_attempt_at.is_(None),
                    PasswordRenewalTask.next_attempt_at <= scheduled_time,
                ),
            )
        )
        return list(result.scalars().all())

    async def complete_task(self, task: PasswordRenewalTask) -> None:
        logger.info(f"Marking task {task.task_id} as completed for device {task.device_id}")
        task.status = PasswordRenewalTaskStatus.COMPLETED
        task.next_attempt_at = None
        await self._save()

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
        task.next_attempt_at = None
        await self._save()

    async def cancel_scheduled_tasks_for_device(self, device: str) -> None:
        await self._session.execute(
            update(PasswordRenewalTask)
            .where(
                PasswordRenewalTask.device_id == device,
                PasswordRenewalTask.status == PasswordRenewalTaskStatus.PENDING,
            )
            .values(status=PasswordRenewalTaskStatus.CANCELED, next_attempt_at=None)
        )
        await self._save()

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
        await self._save()
        return task_id

    async def update_task_error(self, task: PasswordRenewalTask, error_message: str) -> None:
        task.error_count += 1
        task.latest_error = error_message
        if task.error_count >= 5:
            task.status = PasswordRenewalTaskStatus.ERROR
            task.next_attempt_at = None
            logger.error(f"Task {task.task_id} for device {task.device_id} has exceeded error limit.")
        else:
            task.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        await self._save()
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
