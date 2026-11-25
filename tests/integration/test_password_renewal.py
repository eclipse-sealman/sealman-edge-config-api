"""Password API/worker tests use disposable PostgreSQL and mocked SEMS only."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from authorization.permission_types import Device as DevicePermission
from db.models.device import Device
from db.models.password_renewal import PasswordRenewalTask, PasswordRenewalTaskStatus
from db.sqlalchemy.password_renewal_task import SqlAlchemyPasswordRenewalTaskRepository
from routers.password.service import request_password, renew_password
from routers.smart_ems.password_renewal_task_processor import process_password_renewal_task
from routers.smart_ems import password_renewal_task_processor as processor
from tests.integration.test_abac_authorization import AbacFixtures


@pytest.fixture
async def renewal_database(postgres_container, apply_migrations):
    engine = create_async_engine(postgres_container, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    device = f"eg-{uuid4().int}"
    async with sessions() as session:
        session.add(Device(device_id=device, type_id="default", device_data={}))
        await session.commit()
    try:
        yield sessions, device
    finally:
        async with sessions() as session:
            await session.execute(delete(PasswordRenewalTask).where(
                PasswordRenewalTask.device_id == device
            ))
            await session.execute(delete(Device).where(Device.device_id == device))
            await session.commit()
        await engine.dispose()


async def seed_due_task(sessions, device):
    async with sessions() as session:
        repo = SqlAlchemyPasswordRenewalTaskRepository(session)
        async with repo.lock_device(device):
            task_id = await repo.schedule_task(
                device, 456, datetime.now(timezone.utc) - timedelta(days=1)
            )
        return await repo.get_pending_task(task_id, datetime.now(timezone.utc))


@pytest.fixture
def sems():
    with (
        patch("routers.password.service.SmartEMS.get_device_secret", return_value={
            "id": 456, "forceRenewal": False,
            "deviceTypeSecret": {"secretValueRenewAfterDays": 30},
            "createdAt": "2026-01-01T00:00:00+00:00", "updatedAt": None,
        }) as get,
        patch("routers.password.service.SmartEMS.show_device_secret", return_value={
            "id": 456, "secretValue": "synthetic-password",
        }) as show,
        patch("routers.password.service.SmartEMS.force_renew_device_secret") as renew,
    ):
        yield get, show, renew


async def test_concurrent_reveals_create_one_pending_task(renewal_database, sems):
    sessions, device = renewal_database
    with patch("routers.password.service.datetime") as clock:
        clock.now.return_value = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)

        async def reveal():
            async with sessions() as session:
                return await request_password(device, SqlAlchemyPasswordRenewalTaskRepository(session))

        results = await asyncio.gather(*(reveal() for _ in range(8)))
    assert len({result.renewalTask.taskId for result in results}) == 1
    async with sessions() as session:
        tasks = list((await session.scalars(select(PasswordRenewalTask).where(
            PasswordRenewalTask.device_id == device
        ))).all())
        assert len(tasks) == 1
        assert tasks[0].status == PasswordRenewalTaskStatus.PENDING


async def test_concurrent_workers_submit_once(renewal_database, sems):
    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)

    async def work():
        async with sessions() as session:
            await process_password_renewal_task(
                SqlAlchemyPasswordRenewalTaskRepository(session),
                task, datetime.now(timezone.utc),
            )

    await asyncio.gather(*(work() for _ in range(6)))
    sems[2].assert_awaited_once_with(456)
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        assert saved.status == PasswordRenewalTaskStatus.COMPLETED


async def test_concurrent_failed_workers_share_retry_time(renewal_database, sems):
    from exceptions import SEMSError

    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    original_schedule = task.scheduled_time
    selected = 0
    all_selected = asyncio.Event()
    now = datetime.now(timezone.utc)
    # Upstream processing can finish later than the pass's eligibility cutoff.
    failed_at = now + timedelta(minutes=2)
    sems[2].side_effect = SEMSError("Synthetic temporary outage", 503)

    async def work():
        nonlocal selected
        async with sessions() as session:
            repo = SqlAlchemyPasswordRenewalTaskRepository(session)
            tasks = await repo.get_pending_tasks(now)
            candidate = next(row for row in tasks if row.task_id == task.task_id)
            selected += 1
            if selected == 6:
                all_selected.set()
            await asyncio.wait_for(all_selected.wait(), timeout=5)
            await process_password_renewal_task(repo, candidate, now)

    with patch("db.sqlalchemy.password_renewal_task.datetime") as clock:
        clock.now.return_value = failed_at
        await asyncio.gather(*(work() for _ in range(6)))
    sems[2].assert_awaited_once_with(456)
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        assert saved.status == PasswordRenewalTaskStatus.PENDING
        assert saved.error_count == 1
        assert saved.scheduled_time == original_schedule
        assert saved.next_attempt_at == failed_at + timedelta(minutes=30)
        repo = SqlAlchemyPasswordRenewalTaskRepository(session)
        assert task.task_id not in {row.task_id for row in await repo.get_pending_tasks(failed_at)}
        assert await repo.get_pending_task(task.task_id, failed_at) is None


@pytest.mark.parametrize("offset_us", [-1, 0, 1])
async def test_retry_eligibility_boundary(renewal_database, sems, offset_us):
    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    retry_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        saved.next_attempt_at = retry_at
        saved.error_count = 1
        await session.commit()
    cutoff = retry_at + timedelta(microseconds=offset_us)
    async with sessions() as session:
        repo = SqlAlchemyPasswordRenewalTaskRepository(session)
        selected = {row.task_id for row in await repo.get_pending_tasks(cutoff)}
        assert (task.task_id in selected) == (offset_us >= 0)
        await process_password_renewal_task(repo, task, cutoff)
    assert sems[2].await_count == int(offset_us >= 0)
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        assert saved.error_count == 1
        assert saved.next_attempt_at == (None if offset_us >= 0 else retry_at)
        assert saved.status == (
            PasswordRenewalTaskStatus.COMPLETED if offset_us >= 0
            else PasswordRenewalTaskStatus.PENDING
        )


async def test_manual_renewal_does_not_wait_for_automatic_retry(renewal_database, sems):
    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        saved.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        await session.commit()
    async with sessions() as session:
        await renew_password(device, SqlAlchemyPasswordRenewalTaskRepository(session))
    sems[2].assert_awaited_once_with(456)
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        assert saved.status == PasswordRenewalTaskStatus.CANCELED
        assert saved.next_attempt_at is None


@pytest.mark.parametrize("operation", ["manual", "reschedule"])
async def test_waiting_worker_rechecks_after_cancellation_or_reschedule(
    renewal_database, sems, operation
):
    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    holding_lock, release = asyncio.Event(), asyncio.Event()

    async def blocked_upstream(*args):
        holding_lock.set()
        await release.wait()
        return {"id": 456, "secretValue": "synthetic-password"}

    if operation == "manual":
        sems[2].side_effect = blocked_upstream
    else:
        sems[1].side_effect = blocked_upstream

    async def first():
        async with sessions() as session:
            repo = SqlAlchemyPasswordRenewalTaskRepository(session)
            if operation == "manual":
                await renew_password(device, repo)
            else:
                await request_password(device, repo)

    async def worker():
        async with sessions() as session:
            await process_password_renewal_task(
                SqlAlchemyPasswordRenewalTaskRepository(session), task,
                datetime(2026, 11, 17, tzinfo=timezone.utc),
            )

    with patch("routers.password.service.datetime") as clock:
        clock.now.return_value = datetime(2026, 11, 16, 23, tzinfo=timezone.utc)
        first_request = asyncio.create_task(first())
        await asyncio.wait_for(holding_lock.wait(), timeout=5)
        waiting_worker = asyncio.create_task(worker())
        try:
            await asyncio.sleep(0.05)
            assert not waiting_worker.done()
        finally:
            release.set()
        await asyncio.gather(first_request, waiting_worker)
    assert sems[2].await_count == (1 if operation == "manual" else 0)
    async with sessions() as session:
        old = await session.get(PasswordRenewalTask, task.task_id)
        assert old.status == PasswordRenewalTaskStatus.CANCELED
        pending = await SqlAlchemyPasswordRenewalTaskRepository(session).get_pending_tasks_for_device_count(device)
        assert pending == (1 if operation == "reschedule" else 0)


async def test_manual_failure_rolls_back_and_keeps_due_task(renewal_database, sems):
    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    sems[2].side_effect = RuntimeError("synthetic upstream failure")
    async with sessions() as session:
        with pytest.raises(RuntimeError, match="synthetic upstream failure"):
            await renew_password(device, SqlAlchemyPasswordRenewalTaskRepository(session))
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        assert saved.status == PasswordRenewalTaskStatus.PENDING


async def test_worker_stops_after_five_sanitized_failures(renewal_database, sems):
    from exceptions import SEMSError

    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    sems[2].side_effect = SEMSError("SECRET-SHOULD-NOT-APPEAR", 403)
    start = datetime.now(timezone.utc)
    for attempt in range(6):
        now = start + timedelta(minutes=30 * attempt)
        with patch("db.sqlalchemy.password_renewal_task.datetime") as clock:
            clock.now.return_value = now
            async with sessions() as session:
                await process_password_renewal_task(
                    SqlAlchemyPasswordRenewalTaskRepository(session), task, now,
                )
    assert sems[2].await_count == 5
    async with sessions() as session:
        saved = await session.get(PasswordRenewalTask, task.task_id)
        assert saved.status == PasswordRenewalTaskStatus.ERROR
        assert saved.error_count == 5
        assert saved.next_attempt_at is None
        assert saved.latest_error == "Password renewal request failed (HTTP 403)."


async def test_worker_already_pending_upstream_completes_without_resubmitting(renewal_database, sems):
    sessions, device = renewal_database
    task = await seed_due_task(sessions, device)
    sems[0].return_value["forceRenewal"] = True
    async with sessions() as session:
        await process_password_renewal_task(
            SqlAlchemyPasswordRenewalTaskRepository(session), task, datetime.now(timezone.utc),
        )
    sems[2].assert_not_awaited()
    async with sessions() as session:
        assert (await session.get(PasswordRenewalTask, task.task_id)).status == PasswordRenewalTaskStatus.COMPLETED


@pytest.fixture
async def next_renewal_device(renewal_database):
    sessions, _ = renewal_database
    device = f"eg-{uuid4().int}"
    async with sessions() as session:
        session.add(Device(device_id=device, type_id="default", device_data={}))
        await session.commit()
    try:
        yield device
    finally:
        async with sessions() as session:
            await session.execute(delete(PasswordRenewalTask).where(
                PasswordRenewalTask.device_id == device
            ))
            await session.execute(delete(Device).where(Device.device_id == device))
            await session.commit()


@pytest.mark.parametrize("failure_stage", ["lock", "requery", "flush", "commit"])
async def test_worker_database_failure_does_not_skip_next_device_or_purge(
    renewal_database, next_renewal_device, sems, monkeypatch, failure_stage,
):
    sessions, first_device = renewal_database
    first_task = await seed_due_task(sessions, first_device)
    next_task = await seed_due_task(sessions, next_renewal_device)
    expired_task_id = str(uuid4())
    async with sessions() as session:
        session.add(PasswordRenewalTask(
            task_id=expired_task_id, device_id=first_device, secret_id=456,
            scheduled_time=datetime.now(timezone.utc) - timedelta(days=400),
            status=PasswordRenewalTaskStatus.COMPLETED, error_count=0,
            created_at=datetime.now(timezone.utc) - timedelta(days=400),
        ))
        await session.commit()

    original_get_pending = SqlAlchemyPasswordRenewalTaskRepository.get_pending_tasks

    async def ordered_pending(repository, now):
        tasks = await original_get_pending(repository, now)
        return sorted(tasks, key=lambda task: task.device_id != first_device)

    monkeypatch.setattr(SqlAlchemyPasswordRenewalTaskRepository, "get_pending_tasks", ordered_pending)
    opened_sessions = []

    def session_factory():
        session = sessions()
        opened_sessions.append(session)
        if len(opened_sessions) == 2:
            # First processing session; the preceding session only lists due IDs.
            session.rollback = AsyncMock(wraps=session.rollback)
            if failure_stage in {"lock", "requery"}:
                execute = session.execute
                calls = 0

                async def fail_query(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if failure_stage == "lock" and calls == 1:
                        raise TimeoutError("SENSITIVE-FAILURE-DETAIL")
                    if failure_stage == "requery" and calls == 2:
                        return await execute(text("SELECT 1 / 0"))
                    return await execute(*args, **kwargs)

                session.execute = fail_query
            else:
                async def fail_persistence(*args, **kwargs):
                    # Real PostgreSQL failure leaves the transaction aborted.
                    await session.execute(text("SELECT 1 / 0"))

                setattr(session, failure_stage, fail_persistence)
        return session

    monkeypatch.setattr(processor, "AsyncSessionLocal", session_factory)
    with patch.object(processor.logger, "error") as log_error:
        await processor.process_password_renewal_tasks()

    opened_sessions[1].rollback.assert_awaited_once()
    assert len(opened_sessions) == 4  # listing, failed task, next task, purge
    log_error.assert_called_once()
    assert log_error.call_args.args[1:3] == (first_task.task_id, first_device)
    assert "SENSITIVE-FAILURE-DETAIL" not in str(log_error.call_args)
    assert any(call.args[0] == next_renewal_device for call in sems[0].await_args_list)
    assert sems[2].await_count == (1 if failure_stage in {"lock", "requery"} else 2)
    async with sessions() as session:
        failed = await session.get(PasswordRenewalTask, first_task.task_id)
        completed = await session.get(PasswordRenewalTask, next_task.task_id)
        assert failed.status == PasswordRenewalTaskStatus.PENDING
        assert failed.error_count == 0
        assert completed.status == PasswordRenewalTaskStatus.COMPLETED
        assert await session.get(PasswordRenewalTask, expired_task_id) is None


@pytest.mark.parametrize("prefix", ["password", "smartems/secret"])
async def test_core_and_legacy_routes(client, db_session, sems, prefix):
    device = "eg-123456789"
    await db_session.execute(text(
        "INSERT INTO devices (device_id, type_id, device_data) VALUES (:device, 'default', '{}')"
    ), {"device": device})
    await db_session.commit()
    base = f"/{device}/{prefix}"
    info = await client.get(f"{base}/info")
    assert info.status_code == 200, info.text
    assert info.json()["deviceTypeHasAuthSecret"] is True
    revealed = await client.post(f"{base}/request")
    assert revealed.status_code == 200, revealed.text
    assert revealed.json()["secretValue"] == "synthetic-password"
    assert revealed.json()["renewalTask"]["status"] == "Pending"
    status = await client.get(f"{base}/info")
    assert status.json()["renewalTask"]["taskId"] == revealed.json()["renewalTask"]["taskId"]
    renewed = await client.post(f"{base}/renew")
    assert renewed.status_code == 200, renewed.text
    assert renewed.json() == ({"requestAccepted": True} if prefix == "password" else None)
    after = await client.get(f"{base}/info")
    assert after.json()["renewalTask"]["status"] == "Canceled"
    for response in (info, revealed, status, renewed, after):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize("prefix", ["password", "smartems/secret"])
@pytest.mark.parametrize("method,action,permission", [
    ("GET", "info", DevicePermission.READ),
    ("POST", "request", DevicePermission.PASSWORD_READ),
    ("POST", "renew", DevicePermission.PASSWORD_WRITE),
])
@pytest.mark.parametrize("scenario", ["allowed", "outside-scope", "missing-permission"])
async def test_password_routes_enforce_device_scoped_permissions(
    client, db_session, fake_jwt_user, sems, prefix, method, action, permission, scenario,
):
    fake_jwt_user["roles"] = []
    granted = permission if scenario != "missing-permission" else "device.network.write"
    await AbacFixtures(db_session).setup(
        users={"password-user": fake_jwt_user["oid"]},
        scopes={"eu": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
        roles={"password-role": [granted]},
        teams={"password-team": {"scope": "eu", "roles": ["password-role"], "users": ["password-user"]}},
        devices={"eg-123456789": {"region": "EU"}, "eg-987654321": {"region": "US"}},
    )
    device = "eg-987654321" if scenario == "outside-scope" else "eg-123456789"
    response = await client.request(method, f"/{device}/{prefix}/{action}")
    assert response.status_code == (200 if scenario == "allowed" else 403), response.text
    assert response.headers["cache-control"] == "no-store"
    if scenario != "allowed":
        for upstream in sems:
            upstream.assert_not_awaited()


@pytest.mark.parametrize("prefix", ["password", "smartems/secret"])
async def test_upstream_rejection_is_not_cached(client, db_session, sems, prefix):
    from exceptions import SEMSError

    await db_session.execute(text(
        "INSERT INTO devices (device_id, type_id, device_data) VALUES ('eg-123456789', 'default', '{}')"
    ))
    await db_session.commit()
    sems[2].side_effect = SEMSError("Password renewal request rejected", 403)
    response = await client.post(f"/eg-123456789/{prefix}/renew")
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("prefix", ["password", "smartems/secret"])
async def test_unexpected_password_error_is_sanitized_and_not_cached(client, db_session, sems, prefix):
    import httpx
    from main import app

    await db_session.execute(text(
        "INSERT INTO devices (device_id, type_id, device_data) VALUES ('eg-123456789', 'default', '{}')"
    ))
    await db_session.commit()
    sems[2].side_effect = RuntimeError("SECRET-SHOULD-NOT-APPEAR")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as errors_client:
        response = await errors_client.post(f"/eg-123456789/{prefix}/renew")
    assert response.status_code == 500
    assert "SECRET-SHOULD-NOT-APPEAR" not in response.text
    assert response.headers["cache-control"] == "no-store"
