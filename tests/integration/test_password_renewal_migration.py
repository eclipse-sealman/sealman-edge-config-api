from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from testcontainers.postgres import PostgresContainer
import pytest

import constants


def test_duplicate_pending_migration_preserves_rows_and_earliest_deadline(monkeypatch):
    with PostgresContainer("postgres:17.5") as container:
        url = container.get_connection_url().replace("psycopg2", "psycopg")
        monkeypatch.setattr(constants, "POSTGRES_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "c8d2e4f6a1b3")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO devices (device_id, type_id, device_data) "
                    "VALUES ('migration-device', 'default', '{}')"
                ))
                connection.execute(text("""
                    INSERT INTO password_renewal_tasks
                        (task_id, device_id, secret_id, scheduled_time, status, error_count)
                    VALUES
                        ('earliest', 'migration-device', 456, '2026-01-01Z', 'Pending', 0),
                        ('duplicate', 'migration-device', 456, '2026-01-02Z', 'Pending', 2),
                        ('history', 'migration-device', 456, '2025-01-01Z', 'Completed', 0)
                """))
            command.upgrade(config, "head")
            with engine.connect() as connection:
                rows = connection.execute(text(
                    "SELECT task_id, status, error_count FROM password_renewal_tasks ORDER BY task_id"
                )).all()
                assert rows == [
                    ("duplicate", "Canceled", 2),
                    ("earliest", "Pending", 0),
                    ("history", "Completed", 0),
                ]
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text("""
                        INSERT INTO password_renewal_tasks
                            (task_id, device_id, secret_id, scheduled_time, status, error_count)
                        VALUES ('new', 'migration-device', 456, now(), 'Pending', 0)
                    """))
            command.downgrade(config, "c8d2e4f6a1b3")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM password_renewal_tasks")).scalar_one() == 3
        finally:
            engine.dispose()


def test_retry_time_migration_preserves_existing_tasks(monkeypatch):
    with PostgresContainer("postgres:17.5") as container:
        url = container.get_connection_url().replace("psycopg2", "psycopg")
        monkeypatch.setattr(constants, "POSTGRES_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "517b62009cca")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO devices (device_id, type_id, device_data) "
                    "VALUES ('retry-migration', 'default', '{}')"
                ))
                connection.execute(text("""
                    INSERT INTO password_renewal_tasks
                        (task_id, device_id, secret_id, scheduled_time, status, error_count)
                    VALUES
                        ('pending', 'retry-migration', 456, '2026-01-01Z', 'Pending', 2),
                        ('completed', 'retry-migration', 456, '2025-01-01Z', 'Completed', 0)
                """))
                before = connection.execute(text(
                    "SELECT * FROM password_renewal_tasks ORDER BY task_id"
                )).all()
            command.upgrade(config, "head")
            with engine.begin() as connection:
                original_columns = (
                    "task_id, device_id, secret_id, scheduled_time, status, "
                    "error_count, latest_error, created_at, updated_at"
                )
                assert connection.execute(text(
                    f"SELECT {original_columns} FROM password_renewal_tasks ORDER BY task_id"
                )).all() == before
                assert connection.execute(text(
                    "SELECT task_id, next_attempt_at FROM password_renewal_tasks ORDER BY task_id"
                )).all() == [("completed", None), ("pending", None)]
                connection.execute(text(
                    "UPDATE password_renewal_tasks SET next_attempt_at = '2026-01-02Z' "
                    "WHERE task_id = 'pending'"
                ))
                before_downgrade = connection.execute(text(
                    f"SELECT {original_columns} FROM password_renewal_tasks ORDER BY task_id"
                )).all()
            command.downgrade(config, "517b62009cca")
            with engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT * FROM password_renewal_tasks ORDER BY task_id"
                )).all() == before_downgrade
            command.upgrade(config, "head")
        finally:
            engine.dispose()
