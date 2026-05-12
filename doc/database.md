# Database Setup

The API requires a PostgreSQL database. The schema is managed entirely by **Alembic** migrations that run automatically on startup, so no manual schema initialization is needed.

## Local development

Start a PostgreSQL instance with Docker:
```bash
docker compose -f docker-compose.local.yaml up -d
```

This starts Postgres on port **5433** with:
| Setting | Value |
|---|---|
| Host | `127.0.0.1` |
| Port | `5433` |
| Database | `postgres` |
| User | `postgres` |
| Password | `postgres` |
Set the corresponding connection URL in your `.env`:
```env
POSTGRES_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/postgres
```

## Migrations
Alembic migrations run automatically when the application starts. No manual step is required.

## How Migrations Are Implemented

- Alembic config is in [alembic.ini](../alembic.ini).
- Migration environment is in [env.py](../db/env.py).
- Migration files are versioned in [db/migrations/versions/](../db/migrations/versions).
- Migrations are executed on application startup from [main.py](../main.py) via [db/migration.py](../db/migration.py).
- The baseline schema is represented by the initial migration in [db/migrations/versions/](../db/migrations/versions).

## When You Change DB Schema

Follow this order whenever schema changes are introduced.

1. Update SQLAlchemy models in [db/models.py](../db/models.py).
2. Create a migration:
   - Autogenerate for table/column/index changes:
     - `alembic revision --autogenerate -m "describe change"`
   - Manual migration for DB-specific objects (views/functions/triggers) using `op.execute(...)`.
3. Review the generated migration file in [db/migrations/versions/](../db/migrations/versions) and adjust if needed.
4. Apply migration locally:
   - `alembic upgrade head`
5. Verify app startup and DB behavior.

## Manual Migrations for DB-Specific Objects

Use manual migrations when the change cannot be represented by SQLAlchemy metadata/autogenerate.

Typical cases:

- Views (`CREATE OR REPLACE VIEW ...`)
- Functions/procedures (`CREATE FUNCTION ...`)
- Triggers (`CREATE TRIGGER ...`)
- Engine-specific behavior (`UNLOGGED`, extensions, custom SQL)

Recommended pattern:

1. Create an empty revision:
   - `alembic revision -m "add view foo"`
2. Implement SQL explicitly with `op.execute(...)` in `upgrade()`.
3. Add a deterministic `downgrade()` (drop or revert the same object).
4. If needed, guard by dialect:
   - `if op.get_bind().dialect.name == "postgresql": ...`

Example:

```python
from alembic import op

def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE VIEW view_device_snapshot AS
            SELECT device_id FROM devices
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP VIEW IF EXISTS view_device_snapshot")
```

To manage migrations manually:
```bash
# Apply all pending migrations
alembic upgrade head
# Create a new migration (autogenerate from model changes)
alembic revision --autogenerate -m "description"
# Roll back one migration
alembic downgrade -1
```

## Important Notes

- Keep migrations forward-only and deterministic.
- Do not edit an already-applied migration in shared branches; create a new migration instead.
- PostgreSQL-specific SQL is allowed in this project (for example: `JSONB`, views, triggers), but those parts are not portable to other DB engines.
- Autogenerate does not detect all manual SQL objects; always review migrations carefully.