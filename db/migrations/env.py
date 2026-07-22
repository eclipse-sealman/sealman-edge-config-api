import asyncio
import selectors
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context

# Import the metadata from our models so Alembic can autogenerate migrations.
from constants import POSTGRES_URL
import db.models
from db.base import Base

# Alembic Config object — gives access to values in alembic.ini.
config = context.config

# Set up Python logging from the alembic.ini [loggers] section.
# disable_existing_loggers=False ensures the application's own loggers
# (e.g. EdgeConfigAPI) are not silenced when Alembic reconfigures logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The metadata Alembic uses for autogenerate ("alembic revision --autogenerate").
target_metadata = Base.metadata


def _get_url() -> str:
    """Get the PostgreSQL URL from environment."""
    url = POSTGRES_URL
    if not url:
        raise RuntimeError("POSTGRES_URL environment variable is not set.")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection).

    Generates SQL script output instead of executing against the database.
    Useful for reviewing what will be applied before running for real.
    """
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Configure Alembic context and run pending migrations on a sync connection.

    Passed as a callback to AsyncConnection.run_sync() so Alembic's
    synchronous API executes on an already-established async connection.
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine.

    Using an async engine (psycopg v3 async interface) avoids the deadlock
    that occurs when psycopg v3's sync driver is used from a thread-pool
    executor while uvicorn's asyncio event loop is already running.

    asyncio.run() (called below) creates a *fresh* event loop in whichever
    thread Alembic is executing, so psycopg v3 never tries to interact with
    the application's main loop.

    NullPool is used so every connection is closed immediately on release —
    no pool teardown required and no lingering connections.
    """
    connectable = create_async_engine(_get_url(), poolclass=NullPool)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # On Windows the default event loop is ProactorEventLoop, which psycopg v3
    # does not support for async I/O.  Force SelectorEventLoop so psycopg can
    # use select()-based waiting regardless of the platform default.
    asyncio.run(
        run_migrations_online(),
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
    )
