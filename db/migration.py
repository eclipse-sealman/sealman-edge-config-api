import logging

logger = logging.getLogger("EdgeConfigAPI")


def run_migrations() -> None:
    """Apply all pending Alembic migrations up to head.

    Designed to be called from an async context via run_in_executor so it
    runs in a worker thread without blocking the event loop.
    """
    from alembic.config import Config
    from alembic import command

    try:
        logger.info("Running database migrations...")
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        logger.info("Database migrations complete.")
    except Exception as e:
        logger.error(
            f"Database migration failed: {type(e).__name__}: {e}", exc_info=True
        )
        raise
