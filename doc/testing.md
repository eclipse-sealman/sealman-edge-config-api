# Testing

## Prerequisites

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Integration tests also require **Docker** to be running (used by Testcontainers to spin up a real PostgreSQL instance).

---

## Test layout

```
tests/
├── unit/         # Fast, no external dependencies
└── integration/  # Real DB + HTTP; requires Docker
    └── conftest.py  # Shared fixtures (container, session, client)
```

`conftest.py` in the project root pre-populates constants used by unit tests and is kept separate from the integration fixtures.

---

## Running tests

### All tests (unit + integration)

```bash
pytest
```

### Unit tests only

```bash
pytest tests/unit/
```

### Integration tests only

```bash
pytest tests/integration/
```

### Single file

```bash
pytest tests/integration/test_abac_authorization.py -v
```

### With coverage

```bash
pytest --cov=. --cov-report=term-missing
```

The HTML report is written to `htmlcov/` in the project root.

---

## How integration tests work

### Infrastructure (`tests/integration/conftest.py`)

| Fixture | Scope | Purpose |
|---|---|---|
| `postgres_container` | session | Starts a PostgreSQL Docker container once for the entire test run |
| `apply_migrations` | session | Runs `alembic upgrade head` against the container — mirrors production schema exactly |
| `db_session` | function | Opens a per-test `AsyncSession` wrapped in a rolled-back outer transaction for full isolation |
| `client` | function | `httpx.AsyncClient` over `ASGITransport` — calls the real FastAPI app without a server or lifespan side-effects |
| `fake_jwt_user` | function | The JWT payload injected into every request; override in a test module or class to change the calling user |

The app **lifespan is not triggered** by the test client, so Alembic migrations, OIDC discovery, bootstrap tasks, and background jobs never run during tests. Migrations are applied explicitly by `apply_migrations`.

### Test isolation

Repositories call `session.commit()` internally. To keep tests isolated without truncating tables, every test runs inside an outer connection-level transaction that is never committed. A SQLAlchemy event listener re-opens a `SAVEPOINT` after each repository commit, so commits only release savepoints — the outer transaction is rolled back at teardown.

### Authorization in tests

All routes in this project are protected by JWT validation and ABAC permission checks. The integration fixtures bypass real OIDC by overriding `validate_jwt` to return `fake_jwt_user`. By default this is an admin user (bypasses all ABAC checks). Override `fake_jwt_user` in a test class or module to test authorization logic with a non-admin user:

```python
class TestMyFeatureAsViewer:
    @pytest.fixture
    def fake_jwt_user(self):
        return {
            "oid": "viewer-oid",
            "sub": "viewer-oid",
            "preferred_username": "viewer@example.com",
            "name": "Viewer",
            "roles": [],   # no admin flag → ABAC checks apply normally
        }

    async def test_viewer_cannot_write(self, client):
        response = await client.post("/auth/scopes", json={...})
        assert response.status_code == 403
```

### Seeding test data (`AbacFixtures`)

`test_abac_authorization.py` exposes an `AbacFixtures` helper that seeds users, scopes, roles, teams, and devices through the real repositories in a single call. All data is rolled back automatically with the test transaction:

```python
world = await AbacFixtures(db_session).setup(
    users={"viewer": "viewer-oid"},
    scopes={"europe": {"attr": {"region": "EU"}, "access_rule": "ALL"}},
    roles={"reader": ["device.read"]},
    teams={
        "eu-team": {
            "scope": "europe",
            "roles": ["reader"],
            "users": ["viewer"],
        },
    },
    devices={"berlin-01": {"region": "EU"}},
)
# world.teams["eu-team"]  → UUID
# world.scopes["europe"]  → UUID
```

Available action names are seeded by the `seed_actions` Alembic migration. Common values: `device.read`, `device.deployment.write`, `platform.authorization.read`, `platform.authorization.write`.
