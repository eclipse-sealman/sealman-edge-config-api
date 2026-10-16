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

## Extension system side apps

Alongside the public app, the extension system starts one additional,
independent fastapi app:

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
| Authentication (Entra ID / Keycloak / RBAC) | [doc/authentication.md](doc/authentication.md) |
| Database setup & migrations | [doc/database.md](doc/database.md) |
| Running tests & coverage | [doc/testing.md](doc/testing.md) |
