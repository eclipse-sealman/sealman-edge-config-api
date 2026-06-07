"""
Integration-test fixtures for the Edge Configuration API.

Layout
------
postgres_container  (session)  – start a real PostgreSQL container once
apply_migrations    (session)  – run ``alembic upgrade head`` against it
db_session          (function) – per-test AsyncSession with rollback isolation
client              (function) – httpx.AsyncClient over ASGITransport

Isolation strategy
------------------
Repositories commit internally (e.g. ``await session.commit()``).  To keep
tests isolated we wrap every test in an *outer* connection-level transaction
that is never committed.  We also maintain a live SAVEPOINT at all times so
that a repository's ``session.commit()`` only releases the savepoint (it
never touches the outer transaction).  An ``after_transaction_end`` event
listener re-opens a fresh savepoint after each release.  At teardown we
roll back the outer transaction, discarding everything the test wrote.

Auth
----
We override ``validate_jwt`` to return a synthetic admin user dict.  The
real ``ensure_user_exists`` global dependency runs against our test session
and inserts that admin user on the first request.  ``ABACPermissionCheck``
then sees ``is_admin=True`` and bypasses all scope/team checks.

Lifespan
--------
``httpx.ASGITransport`` sends ASGI requests directly to the app without
triggering the Starlette/FastAPI lifespan, so Alembic, OIDC discovery,
bootstrap tasks, and background jobs are never started during tests.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
import warnings

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from pydantic.warnings import PydanticDeprecatedSince20

warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)


# ---------------------------------------------------------------------------
# Session-scoped: start container + run migrations once per test run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container() -> Generator[str, None, None]:
    """
    Spin up a PostgreSQL container and yield its async-compatible connection
    URL.  The container lives for the entire pytest session.
    """
    with PostgresContainer("postgres:17.5") as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        url = (
            f"postgresql+psycopg://{container.username}:{container.password}"
            f"@{host}:{port}/{container.dbname}"
        )
        yield url


@pytest.fixture(scope="session")
def apply_migrations(postgres_container: str) -> None:
    """
    Run all Alembic migrations against the container DB exactly once.

    We temporarily patch ``constants.POSTGRES_URL`` so that env.py picks
    up the container URL when Alembic re-executes the script.  The patch is
    restored afterwards so the rest of the application is unaffected.
    """
    import constants
    from alembic import command
    from alembic.config import Config

    _original = constants.POSTGRES_URL
    constants.POSTGRES_URL = postgres_container
    try:
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
    finally:
        constants.POSTGRES_URL = _original


# ---------------------------------------------------------------------------
# Function-scoped: per-test async session with savepoint-based isolation
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session(
    postgres_container: str,
    apply_migrations: None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession whose outer connection transaction is rolled back
    after the test, restoring the DB to a clean state.

    A SAVEPOINT is kept alive at all times so repository-level commits only
    release savepoints (they never commit the outer transaction).
    """
    engine = create_async_engine(postgres_container, poolclass=NullPool)

    conn = await engine.connect()
    outer = await conn.begin()  # outer transaction – rolled back on teardown

    session = AsyncSession(bind=conn, expire_on_commit=False)
    await session.begin_nested()  # initial SAVEPOINT

    @event.listens_for(session.sync_session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        # After a SAVEPOINT is released (by session.commit()) or rolled back,
        # re-open a new one so the *next* commit also hits a savepoint rather
        # than the outer transaction.
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        await outer.rollback()  # discard everything written during the test
        await conn.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Function-scoped: overridable JWT user + async HTTP client
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_jwt_user() -> dict:
    """
    The synthetic JWT payload injected into every request made by ``client``.

    Override this fixture in a test module or class to change the calling user
    for authorization tests::

        @pytest.fixture
        def fake_jwt_user():
            return {
                "oid": "viewer-oid",
                "sub": "viewer-oid",
                "preferred_username": "viewer@test.com",
                "name": "Viewer",
                "roles": [],   # no admin flag → ABAC checks apply normally
            }

    The default is an admin user (``roles: ["user.admin"]``) that bypasses all
    ABAC permission checks, which is suitable for tests that focus on business
    logic rather than authorization.
    """
    return {
        "oid": "test-admin-oid",
        "sub": "test-admin-oid",
        "preferred_username": "admin@test.com",
        "name": "Test Admin",
        "roles": ["user.admin"],
    }


@pytest.fixture
async def client(
    db_session: AsyncSession,
    fake_jwt_user: dict,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    Yield an ``httpx.AsyncClient`` that calls the FastAPI app via
    ``ASGITransport`` (no real HTTP server, no lifespan side-effects).

    Dependency overrides
    ~~~~~~~~~~~~~~~~~~~~
    * ``get_db``       – yields the per-test session (transaction isolation)
    * ``validate_jwt`` – returns ``fake_jwt_user`` (override that fixture to
                         change the calling user for authorization tests)

    The real ``ensure_user_exists`` global dependency still runs; it uses the
    overridden ``validate_jwt`` and the test session to create the user row so
    that ``ABACPermissionCheck`` can find it.
    """
    from auth import validate_jwt
    from db.session import get_db
    from main import app

    async def _override_get_db():
        yield db_session

    async def _override_validate_jwt():
        return fake_jwt_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[validate_jwt] = _override_validate_jwt

    # ASGITransport sends requests directly to the ASGI app.  It does NOT
    # call the Starlette lifespan, so startup/shutdown handlers are skipped.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
