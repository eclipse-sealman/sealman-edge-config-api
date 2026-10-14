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
extension-startup regressions. Local core tests do not establish production protocol
schema parity, consumer/edge changes, live-device behavior, release/deployment or
cloud/OIDC connectivity; they do not replace Owner review.

### IIOT-2916 neutral template conformance

`tests/integration/test_extension_templates.py` verifies the feature-branch
protocol-neutral template contract through the real `/extensions/schema`,
registration/enable/replace/disable/deregister APIs, public ASGI dispatch and
OpenAPI generation. Test-only FastAPI/Pydantic models generate provider OpenAPI;
function-scoped `ThreadingHTTPServer` instances bind only to allowlisted
`127.0.0.1` loopback ports and serve a read-only catalog and a separate full-CRUD
catalog. No protocol provider, production catalog, credentials, cloud/device
service or additional dependency is needed.

The suite uses the normal integration Testcontainers PostgreSQL/migration and
outer-transaction rollback fixtures. Since `ASGITransport` skips lifespan, the
startup contract is exercised by explicitly calling
`extensions.setup.hydrate_all_routes()` against the isolated session. Synthetic
JWT identities bypass OIDC only; real non-admin users, action rows, roles and
teams are seeded through `AbacFixtures` and the real repositories. A separate
missing-bearer case proves rejection before OIDC initialization or provider I/O.
The only unreachable-source case targets a closed loopback port; no production
network endpoint is mocked or contacted.

The tests assert route/query/body/response forwarding, provider error and core
502 behavior, nested request/response OpenAPI references, independent per-action
ABAC, no automatic role grants, exact all-extension route ownership conflicts,
replacement/disable/deregister cleanup, and cache cleanup. Teardown closes each
loopback server and upstream HTTP client, removes only test-owned extension
routes, and restores dependency, module-registry, validator-cache and OpenAPI
state even after assertion failure. These generic fixtures are core conformance
evidence; downstream provider implementers still own provider-specific catalog,
schema and registration acceptance.

Run the focused suite and adjacent lifecycle/dispatch regressions with the repo's
existing development environment and Docker:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration/test_extension_templates.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration/test_extensions_dispatch.py tests/integration/test_extensions_dynamic_routes.py tests/integration/test_extensions_management_routes.py tests/integration/test_module_schema_lifecycle.py -q -p no:cacheprovider
```

### IIOT-2913 shared direct-method discovery

`tests/unit/extensions/test_direct_method_discovery.py` covers persisted selection,
exact module/method grouping, authorized aliases, scoped/unscoped/no-action
invocation policy, safe projection, typed empty responses and fresh permission
reads. Local JSON pointers, anchors, recursive definitions and embedded resource
IDs resolve entirely within the descriptor and are returned unchanged. New public
IoT Edge registrations still require scoped actions; unscoped/no-action runtime
semantics are tested with persisted-row unit fixtures, not relaxed registration.

`tests/integration/test_extension_direct_method_discovery.py` uses real management
lifecycle, PostgreSQL repositories and non-admin ABAC users/roles/device metadata.
It checks replacement, role/device changes, deregistration, explicit startup
hydration and static typed OpenAPI uniqueness. Normal JWT is required without
`Extension.READ`. Missing/invalid authentication or a token without `oid`/`sub`
keeps the shipped global `401`; discovery user-resolution failure is `403`;
unknown extension/device is `404`; missing/empty/repeated `device_id` is `400`.
Only the unresolved-user HTTP test bypasses provisioning with a test-only
dependency override; production authentication and global handlers are unchanged.

For an **authorized** non-self-contained body, the approved response is exactly
HTTP `500` with `{"message": "This route's body schema is misconfigured"}`.
Tests cover remote and dangling references, ID-only server error logging,
no schema/route ID/exception/traceback in the response, unauthorized/device-filtered
and disabled non-trigger cases, and unchanged local-reference bodies even when
another extension owns a bad schema. Fail-on-use mocks guard dispatch, upstream
schema retrieval, health checks, and Blob/IoT Hub boundaries. No unresolved body
is returned and no remote reference is fetched.

`test_opcua_discovery_actual_handler_subset_full_twelve_and_dto_parity` in
`tests/integration/test_opcua_extension.py` consumes the hashed provider manifest,
DTO exports and captured `src/app.ts` handler inventory in
`tests/fixtures/opcua/provenance.json`. It asserts the real read-ten/write-two/full-
twelve subsets, exact module/bindings/body/query metadata, non-admin device-scope
filtering and zero provider/cloud effects. These are registration-backed local
contract checks, not live hardware or cloud execution evidence.

Run with the existing pinned Python environment and Docker/Testcontainers:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/extensions/test_direct_method_discovery.py tests/integration/test_extension_direct_method_discovery.py tests/integration/test_opcua_extension.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit/extensions/ -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration/test_extensions_management_routes.py tests/integration/test_extensions_dynamic_routes.py -q -p no:cacheprovider
git diff --check
```

The ASGI client does not run full lifespan; hydration is explicitly exercised
against the isolated session. Synthetic JWT and cloud fixtures do not establish
live OIDC/Blob/IoT Hub/device readiness. Independent review and whole-plan
acceptance remain separate from these focused gates.

### IIOT-2913 OPCUA reported projection adoption (WP-8 core)

`tests/integration/test_opcua_extension.py` consumes the committed provider
`47359569edf53bdcf55c50591916c3cda35a655a` without importing or executing sibling
provider/runtime code. `tests/fixtures/opcua/provenance.json` records byte hashes,
source paths, the original runtime revision and prior provider receipts. The four
`reported-*.json` captures come from the original `ConfigurationStateReporter`
through the mocked IoT adapter and fake timers: operational, warning, failed
(including the invalid-configuration message), and removed/empty conditions.
These are runtime captures, not synthesized hardware health observations.

A test-only IoT Hub merge/deletion oracle applies each exact emitted patch to a
previous twin. `configs: null` deletes the legacy member; arrays replace stale
conditions, and omitted private `configId` survives only in the effective twin.
The effective twin is replayed through real authenticated registration and the
public `GET /{device}/twin/identity/seal-module-opcua-client/reported`, using the
real core helper with only IoT Hub HTTP/auth settings mocked. Real allowlisted
loopback HTTP serves the copied OpenAPI during management/hydration; schema
snapshots persist in disposable PostgreSQL. No provider reported endpoint is
invented and no wizard code is changed.

The suite checks exact module/URL/15-second helper deadline, literal conditions
and messages, metadata precision/offset preservation, omitted message,
metadata-only, absent/null/empty reported sections, and repeatable stale
snapshots. Metadata/partial/no-data variants are synthetic projection checks,
not additional runtime emissions. An old `lastUpdated` is returned unchanged:
there is **no freshness, current acquisition or healthy-hardware guarantee**.
The no-conditions operational capture is likewise not proof of acquired data.

Non-admin repository-seeded callers need scoped `device.read`, independently of
OPCUA method/template or configuration-write actions. Denials remain `403`
before invalid-data `500` or claimed/unavailable `503`. Active invalid data
returns the existing safe mismatch `500`, while unregistered, no-schema,
configuration-only, disabled, ref-removed and deregistered states use the
inherited generic projection. Replace/restore and explicit startup hydration
exercise the persisted reported contract and validator-cache lifecycle; the
narrower replacement document is explicitly test-only. Existing missing-module,
backend, network and JSON-error `500` wrappers are preserved, including the
shipped APIError handler's stringified error dictionary under `message`.

Every accepted GET has only the exact mocked IoT Hub read; denied/unavailable
GETs have none. Counters/observers assert no request-time provider/schema/health
calls, Blob operations, direct methods, uploads or twin writes. The returned
runtime projection excludes full-twin/desired/configId envelopes and does not
invent diagnostic(s), defaults or timestamps. Fixture/hash tests and management
fixtures restore shared state and roll back test-only authorization rows.

Run with the existing Python 3.14 `.venv`, pinned requirements and Docker:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration/test_opcua_extension.py tests/integration/test_module_config.py tests/integration/test_module_schema_lifecycle.py tests/integration/test_extension_direct_method_discovery.py tests/integration/test_extension_templates.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/unit -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/integration -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m alembic heads
git diff --check
```

These are local core integration checks, not whole-plan verification. Live Blob
catalog parity still needs secure Owner settings; live cloud/device/JWT/OIDC
smoke, independent review, Owner review/role enablement and an explicit Owner
no-test-CI exception remain pending. Previously accepted runtime-only NodeId,
host/port and PEM checks are unchanged; the shipped empty-PEM example remains a
runtime rejection. No runtime/provider production code, credentials, production
role grants, cloud calls or dependency changes are part of this core stage.
