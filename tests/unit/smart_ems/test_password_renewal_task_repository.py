from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from db.models.password_renewal import PasswordRenewalTask, PasswordRenewalTaskStatus
from db.sqlalchemy.password_renewal_task import SqlAlchemyPasswordRenewalTaskRepository


@pytest.mark.asyncio
async def test_update_task_error():
    mock_session = MagicMock()
    mock_session.commit = AsyncMock()
    repo = SqlAlchemyPasswordRenewalTaskRepository(mock_session)

    task = PasswordRenewalTask(
        task_id="task-1",
        device_id="device-1",
        secret_id=1,
        scheduled_time=datetime.now(timezone.utc),
        status=PasswordRenewalTaskStatus.PENDING,
        error_count=4,
    )
    error_message = "Connection timeout"

    await repo.update_task_error(task, error_message)

    assert task.error_count == 5
    assert task.latest_error == error_message
    assert task.status == PasswordRenewalTaskStatus.ERROR
    mock_session.commit.assert_awaited_once()

