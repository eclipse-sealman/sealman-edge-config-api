# Edge Config API

A FastAPI service for managing IoT Edge device module configurations via IoT Hub module twins and Azure Blob Storage. It exposes a REST API consumed by the Edge Config UI and third-party applications to push configuration files to edge devices asynchronously.

## Prerequisites

- Python 3.13+
- Running PostgreSQL instance (see [Database Setup](doc/database.md))
- Authentication provider configured (Entra ID or Keycloak — see [Authentication](doc/authentication.md))

Create a `.env` file and fill in the required values before starting:

```bash
cp .env.example .env   # adjust IOT_HUB_NAME, SAS_TOKEN, POSTGRES_URL, auth variables, …
```

## Run locally

### 1. Create and activate a virtual environment

**Windows**
```powershell
python -m venv .venv
.venv\Scripts\activate
```

**Linux / macOS**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

**For local development** (includes testing tools):
```bash
pip install -r requirements-dev.txt
```

**For production** (runtime dependencies only):
```bash
pip install -r requirements.txt
```

### 3. Start the server

**Option A — uvicorn**
```bash
python -m uvicorn main:app --host localhost --port 5000
```

**Option B — dev server** (host `localhost`, port `5000`)
```bash
python main.py
```

OpenAPI docs: <http://localhost:5000/docs>

## Device passwords

Password management is a core API, independent of SmartEMS configuration routes.
It still uses SEMS for live secret metadata and renewal requests.

| Route | Device-scoped permission | Result |
|---|---|---|
| `GET /{device}/password/info` | `device.read` | Existing secret metadata plus nullable `renewalTask` |
| `POST /{device}/password/request` | `device.password.read` | `id`, `secretValue`, nullable `renewalTask` |
| `POST /{device}/password/renew` | `device.password.write` | `{"requestAccepted": true}` |

Legacy `/{device}/smartems/secret/{info,request,renew}` routes delegate to the
same core service and keep their existing response fields; legacy renew still
returns JSON `null`. Password responses, including errors, use `Cache-Control:
no-store`. Clients must not persist revealed passwords.

Sealman permission denials return HTTP 403. SEMS errors during password information,
reveal or manual renewal requests return HTTP 502 with a sanitized, operation-specific
`message`, so clients can offer retry instead of treating an upstream refusal as a
missing Sealman permission. Scheduled workers retain the upstream status in their
sanitized retry diagnostics.

Revealing a password automatically schedules a one-time renewal at **23:59:59 UTC
today before 23:00 UTC, or tomorrow at/after 23:00 UTC**. Before 23:00, an existing
pending task is retained; at/after 23:00 it is canceled and replaced. When SEMS
already reports `forceRenewal: true`, revealing does not schedule another task.
Manual renewal cancels scheduled tasks only after SEMS accepts the request (or
already reports a pending renewal); failures leave them intact.

`renewalTask` contains `taskId`, `scheduledTime`, `status`, `errorCount`,
`latestError`, `createdAt`, and `updatedAt`. Times are UTC ISO-8601 strings.
It selects the pending task if present, otherwise the most recently created task.
Statuses are `Pending`, `Canceled`, `Completed`, and `Error`. **`Completed` means
the scheduled renewal request was accepted/already pending in SEMS, not that the
device rotated its password.** Likewise, `requestAccepted` is not rotation
confirmation. `forceRenewal` and secret timestamps are refreshed from SEMS;
`updatedAt` alone is not reliable proof of rotation. Refresh info after actions.
Five failed worker attempts mark a task `Error`; errors exposed to clients are
generic and never include raw historical upstream responses. No configurable
schedule, recurrence, or rotation confirmation tracking is provided.

Workers retain their existing 30-minute polling cadence, so the scheduled time
is a due time, not an exact execution guarantee. Device-row database locks
serialize reveals, manual renewal and worker requests across processes; workers
recheck pending status after locking. Each due task uses a separate database
session, so a lock or persistence failure for one device is logged without
skipping the remaining due devices or completed-task cleanup.
After an upstream failure, the task's failure count and `next_attempt_at` are
saved together under the device lock. Selection and the post-lock recheck both
require this retry time to have arrived, so competing workers cannot immediately
consume the remaining attempts. Retries become eligible 30 minutes after failure
and run on a subsequent worker pass; the original `scheduled_time` is unchanged.
Existing tasks with no retry time remain eligible when due. Terminal tasks have
no next attempt. Deploy the migration before the new worker code and update all
worker replicas: older code does not enforce retry timing.
No in-memory claims or new tracking table
are used. The migration preserves all tasks, cancels duplicate pending rows
while retaining the earliest deadline, and enforces one pending task per device
with a partial unique index. A process failure releases its database lock; as
with any external request, a crash between SEMS acceptance and local commit can
require a retry (the worker first checks SEMS `forceRenewal`).

## Device field defaults

Device defaults initialize stored metadata; they are not substituted on reads.
Creation fills absent fields from the device type's effective defaults before validation
and scope evaluation. Fields from the `default` type still apply to other types, with
type-specific definitions taking precedence.

Adding a field with a default, or assigning its first default, fills only absent keys on
devices of the affected types, in the same transaction as the type edit. Existing values
(including explicit null, false, zero, and empty strings) are preserved. Changing an
existing default affects only future devices. Removing defaults/definitions does not
erase stored metadata, and clearing an optional device value does not reapply a default.
A device's type is assigned at creation and cannot be changed afterwards; editing the
type definition remains supported.

Type edits validate the final effective field definitions under lock, before backfilling
or committing. Backfill rejects non-object metadata on affected device types and aborts
without committing partial changes.

For legacy imports or existing devices with unstored defaults, use the
[explicit cutover backfill](scripts/README.md#device-defaults-cutover-backfill).
There is no automatic startup data migration.

## Endpoint and service defaults

Endpoint/service scalar defaults now follow the same persisted lifecycle: creation fills
missing keys before validation; adding a defaulted field or its first default backfills
only missing keys on instances of that type. Explicit null, false, zero and empty strings
are preserved. Changing a default does not overwrite existing data. Sending
`{"default": null}` in a field-definition PATCH removes its default without deleting stored
values. Ordinary instance PATCHes never refill cleared scalar keys, and reads/search return
stored values without substituting defaults.

Endpoint list fields keep their existing JSONB representation; no new table is required.
Defaults belong to scalar columns, not to the list itself. Endpoint creation and new rows
initialize missing column values. Existing-row updates replace the submitted list but do
not refill missing/null cells; retain row IDs to identify existing rows. Introducing a
column's first default backfills missing cells in existing rows, preserving row IDs, order
and custom properties. Defaults never create lists or rows. Endpoint retyping initializes
missing fields/cells using the target schema, preserving existing values/custom data and
explicit clears (`null`); incompatible retained data is still rejected.

Type edits/backfills lock schemas before instances and commit atomically. Malformed legacy
data in affected fields aborts the backfill rather than being silently normalized. Persisted
default IPs/ports become actual configured network identities, just like explicit values;
unconfigured type defaults remain scan suggestions.

Before deploying this read-behavior change over existing data, use the dry-run
[asset cutover backfill](scripts/README.md#endpoint-and-service-defaults-cutover-backfill),
then explicitly apply it with writes paused. Missing historical values cannot distinguish
never-initialized data from previously cleared keys; review that policy before applying.
There is no automatic startup backfill or stored-value clearing utility.

## Extension system side apps

Alongside the public app, the extension system starts two additional,
independent fastapi apps:

- **internal** (`EXTENSIONS_INTERNAL_API_HOST`/`PORT`, default `127.0.0.1:8500`):
  service-to-service routes, authenticated with `X-Internal-Key`.
- **field ingress** (`EXTENSIONS_FIELD_API_HOST`/`PORT`, default `127.0.0.1:8501`):
  `device` routes called by edge modules, authenticated with a per-extension,
  per-device `X-Device-Key` (issued via `POST /extensions/{name}/device-keys`).
  Sealman resolves the device from the key and forwards it to the upstream as a
  trusted `X-Device-Id`. Expose this port only to the device network path.

Extension details expose initial internal-key issuance and latest rotation timestamps
when known. Device-key listings include UUIDs, optional labels/descriptions and
revocation history, never secrets or hashes. Reissuing atomically revokes the previous
key: only one key per extension/device is active. Revoke a specific record through
`DELETE /extensions/{name}/device-keys/by-id/{key_id}`; the existing device-based
revoke route remains supported. Revoked credentials are rejected immediately.
History is removed with its owning device or extension.

Set `EXTENSIONS_ENABLED=false` to disable extensions completely. The
`/extensions` management API is not registered, persisted routes and fetched schemas
are not hydrated/refreshed, and the internal side app is not started. Persisted data is
left untouched and becomes active again when the subsystem is re-enabled.

The loopback default only serves callers in the same network namespace. A container
deployment that requires other containers to call this app must explicitly set
`EXTENSIONS_INTERNAL_API_HOST=0.0.0.0`. **Never publish port 8500 publicly or add an
external reverse-proxy/ingress rule for it.** When binding beyond loopback, enforce
isolation with a dedicated internal network, firewall rules, or `NetworkPolicy`;
application keys are not a substitute for network isolation.

**Run the API only as a single process when extensions are enabled.** Mounted extension
routes live in one process's memory: register/enable/disable/replace/delete only take
effect in the process that handled the request (others catch up on restart), and only
one process can bind the internal port. Do not use `--workers`/`WEB_CONCURRENCY` > 1 or
multiple replicas.


## Further documentation

| Topic | File |
|---|---|
| Architecture & module config mechanism | [doc/architecture.md](doc/architecture.md) |
| Authentication (Entra ID / Keycloak) | [doc/authentication.md](doc/authentication.md) |
| Authorization (actions, teams, scopes, extension keys) | [doc/authorization.md](doc/authorization.md) |
| Database setup & migrations | [doc/database.md](doc/database.md) |
| Running tests & coverage | [doc/testing.md](doc/testing.md) |
