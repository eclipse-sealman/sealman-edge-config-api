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

## Further documentation

| Topic | File |
|---|---|
| Architecture & module config mechanism | [doc/architecture.md](doc/architecture.md) |
| Authentication (Entra ID / Keycloak / RBAC) | [doc/authentication.md](doc/authentication.md) |
| Database setup & migrations | [doc/database.md](doc/database.md) |
| Running tests & coverage | [doc/testing.md](doc/testing.md) |
