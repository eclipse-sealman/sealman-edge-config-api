from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from testcontainers.postgres import PostgresContainer

import constants


def test_key_lifecycle_migration_preserves_credentials_and_round_trips(monkeypatch):
    with PostgresContainer("postgres:17.5") as container:
        url = (
            f"postgresql+psycopg://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}/{container.dbname}"
        )
        monkeypatch.setattr(constants, "POSTGRES_URL", url)
        config = Config("alembic.ini")
        command.upgrade(config, "f1a2b3c4d5e6")
        extension_id = uuid4()
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
            connection.execute(
                "INSERT INTO extensions (id, name, description, schema_version, enabled, internal_key_hash) "
                "VALUES (%s, 'migration-key-test', '', 1, false, 'synthetic-internal-hash')",
                (extension_id,),
            )
            # Use the migration-seeded default type, not a local application database.
            connection.execute(
                "INSERT INTO devices (device_id, device_data, type_id) VALUES ('migration-device', '{}', 'default')"
            )
            connection.execute(
                "INSERT INTO extension_device_keys (extension_id, device_id, key_hash, module_id, created_at) "
                "VALUES (%s, 'migration-device', 'synthetic-device-hash', 'module-1', '2026-01-01T00:00:00Z')",
                (extension_id,),
            )

        command.upgrade(config, "head")
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
            row = connection.execute(
                "SELECT id, key_hash, module_id, created_at, revoked_at FROM extension_device_keys"
            ).fetchone()
            assert row[0] is not None
            assert row[1:3] == ("synthetic-device-hash", "module-1")
            assert row[3].isoformat() == "2026-01-01T00:00:00+00:00"
            assert row[4] is None
            assert connection.execute(
                "SELECT internal_key_hash, internal_key_created_at, internal_key_rotated_at FROM extensions"
            ).fetchone() == ("synthetic-internal-hash", None, None)
            connection.execute(
                "UPDATE extension_device_keys SET revoked_at=now() WHERE id=%s", (row[0],),
            )
            connection.execute(
                "INSERT INTO extension_device_keys (extension_id, device_id, key_hash, label, description) "
                "VALUES (%s, 'migration-device', 'replacement-hash', 'Uploader', 'Replacement credential')",
                (extension_id,),
            )

        engine = create_engine(url)
        try:
            schema = inspect(engine)
            assert schema.get_pk_constraint("extension_device_keys")["constrained_columns"] == ["id"]
            active_index = next(i for i in schema.get_indexes("extension_device_keys")
                                if i["name"] == "uq_extension_device_keys_active")
            assert active_index["unique"]
            assert "revoked_at IS NULL" in active_index["dialect_options"]["postgresql_where"]
        finally:
            engine.dispose()

        command.downgrade(config, "f1a2b3c4d5e6")
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
            assert connection.execute(
                "SELECT extension_id, device_id, key_hash FROM extension_device_keys"
            ).fetchall() == [(extension_id, "migration-device", "replacement-hash")]
            assert connection.execute(
                "SELECT internal_key_hash FROM extensions"
            ).fetchone() == ("synthetic-internal-hash",)
        command.upgrade(config, "head")
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as connection:
            assert connection.execute(
                "SELECT key_hash, revoked_at FROM extension_device_keys"
            ).fetchall() == [("replacement-hash", None)]
