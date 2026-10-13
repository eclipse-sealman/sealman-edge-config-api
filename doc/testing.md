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

### Combined module-schema lifecycle

`tests/integration/test_module_schema_lifecycle.py` joins the previously separate
module and dynamic-response contracts on the WP-1–WP-4 core behavior:

- Function-scoped test-only FastAPI/Pydantic models generate the provider OpenAPI
  document. A real allowlisted loopback `ThreadingHTTPServer` serves it and a
  neutral, non-template probe. Schema-fetch/proxy counters prove module operations
  do not contact that service at request time. `response_ref` remains documentation
  only; the probe's deliberately different response is relayed unchanged.
- The real management API (via the shared ASGI client) registers disabled providers,
  enables/replaces/disables/re-enables/deregisters them, and persists snapshots in
  Testcontainers PostgreSQL. The complete [manifest example](extension/manifest.md)
  is validated against the authenticated `/extensions/schema` and exercised through
  registration, runtime and OpenAPI. Separate providers reuse component names
  without sharing module/purpose namespaces.
- One in-memory fake Blob store connects POST uploads to configuration GET downloads.
  Only external Blob/IoT Hub I/O and synthetic storage/auth settings are replaced;
  the real parser, schema validator, upload helper, UUID and desired-twin patch
  construction run. Neutral configuration and both deprecated fixed IDs retain
  supplied nulls/extras without defaults, coercion or pruning. Configuration GET
  still has its inherited object/`null` shape, not a newly broadened all-JSON contract.
- `AbacFixtures` seeds non-admin users, roles, teams and device scopes through real
  repositories. Read/write module actions and the probe action are independent;
  registering an action does not grant it. A separate no-credentials case removes
  only the JWT identity override and checks rejection by the real public dependency
  chain before OIDC or cloud I/O. Synthetic identity is not live JWT/OIDC verification.
- Tests explicitly call `extensions.setup.hydrate_all_routes()` with the session
  factory bound to the isolated transaction. This exercises the startup hydration
  entrypoint, **not** the entire application lifespan, OIDC discovery or background
  jobs. Failed purpose refreshes keep last-valid snapshots independently; corrupted
  persisted syntax remains claimed/unavailable before cloud effects.
- Teardown closes loopback servers and the per-test HTTP upstream client, unmounts
  only owned extensions, and restores registry, validator-cache, OpenAPI and temporary
  dependency overrides even after assertion failure. Database changes roll back.

Run with the existing development environment and Docker, without a production
`.env` or cloud/device/OIDC credentials:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration/test_module_schema_lifecycle.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m alembic heads
git diff --check
```

Existing module/extension tests remain the evidence for detailed malformed-body,
size-limit, helper-error, local-ref, deep/surrogate/`_sa` serialization and disabled
extension-startup regressions. These local tests do not verify production protocol
schema parity, templates/CRUD/collisions, consumer/edge changes, live devices,
release/deployment or cloud/OIDC connectivity; they do not replace Owner review.
