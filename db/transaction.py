"""One Session transaction around repository methods that commit themselves.

SQLAlchemy's Session is already the unit of work. This module only controls
when that Session commits. It follows the begin-once pattern in the Session
transaction docs, adapted to this codebase:

- ``Session.begin()`` commits on the way out and rolls back if the block or
  the commit fails. It raises if a transaction is already open.
- ``Session.commit()`` and ``Session.rollback()`` always act on the outermost
  transaction. An inner ``commit()`` would end the block early, so repository
  methods must call ``commit_session`` instead of ``session.commit()``.

``release_read_transaction`` ends an autobegun read before a network call.
It must not run inside ``atomic_session``: rollback goes to the root and would
discard the write in progress.
"""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

_DEFER_COMMIT = "defer_commit"


def in_atomic(session: AsyncSession) -> bool:
    return bool(session.info.get(_DEFER_COMMIT))


async def commit_session(session: AsyncSession) -> None:
    """Commit, or flush when this session is inside ``atomic_session``."""
    if in_atomic(session):
        await session.flush()
    else:
        await session.commit()


async def release_read_transaction(session: AsyncSession) -> None:
    """Return the pooled connection after a read, before network I/O.

    A SELECT autobegins and holds a connection until commit or rollback.
    Callers must already have copied what they need into plain values.
    """
    if in_atomic(session):
        raise RuntimeError("release_read_transaction() cannot run inside atomic_session()")
    if session.in_transaction():
        await session.rollback()


@asynccontextmanager
async def atomic_session(session: AsyncSession):
    """Commit the block once, or roll it back if anything raises."""
    if in_atomic(session):
        # Nested block. Do not commit here: that would commit the outer one.
        yield
        return

    session.info[_DEFER_COMMIT] = True
    try:
        if not session.in_transaction():
            async with session.begin():
                yield
            return

        # A transaction is already open: an earlier SELECT, or the integration
        # test SAVEPOINT. begin() would raise, so finish this one instead.
        try:
            yield
        except Exception:
            await session.rollback()
            raise
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    finally:
        session.info.pop(_DEFER_COMMIT, None)
