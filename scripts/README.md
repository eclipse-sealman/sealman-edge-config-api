# import_devices.py

Reads all **IoT Edge devices** from Azure IoT Hub and upserts them into the
`devices` table of the PostgreSQL database.

Only the tag keys that are defined in the `platform_meta` column of the
`platform` table (row `name = 'default'`) are imported — all other IoT Hub
tags are ignored.

Note: this script might be refactored once endpoint to create/update devices is available in the API. 
For now, it serves as a one-off utility to bootstrap the database with existing IoT Hub devices.

## How it works

### 1 — Read allowed keys from `platform_meta`

Before touching IoT Hub, the script reads the `default` row from the
`platform` table and extracts the **key names** from its `platform_meta` JSON:

```sql
SELECT platform_meta FROM platform WHERE name = 'default';
-- e.g. {"city": null, "countryCode": null, "description": null, "geoLocation": null}
```

Those key names become the **allow-list** for the import.

### 2 — Fetch IoT Edge devices from IoT Hub

All IoT Edge device twins are retrieved from Azure IoT Hub using the query:

```sql
SELECT * FROM devices WHERE capabilities.iotEdge = true
```

### 3 — Filter tags and upsert

For each device, only the tag keys present in the allow-list are written to
`device_meta`. Any extra IoT Hub tags are dropped.

### Merge strategy (idempotent)

| Situation | Behaviour |
|---|---|
| Device **not** in DB | `INSERT` — filtered IoT Hub tags stored as `device_meta` |
| Device **already** in DB | `UPDATE` — new keys are **added**; existing DB values are **never overwritten** |

Running the script multiple times with the same IoT Hub data always produces the
same final state.

---

## Prerequisites

[`uv`](https://docs.astral.sh/uv/) is the only requirement. If it is not installed yet:

**Windows (PowerShell)**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux / macOS**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Restart your terminal afterwards so the `uv` command is on your `PATH`.

Dependencies (`psycopg[binary]`, `httpx`, `python-dotenv`) are declared directly inside
`import_devices.py`:

```python
# /// script
# requires-python = ">=3.11, <3.14"
# dependencies = [
#   "psycopg[binary]>=3.3.3",
#   "httpx>=0.27.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
```

`uv run` reads this block and installs them into a **cached ephemeral environment** automatically —
no `requirements.txt`, no manual `venv`.

---

## Configuration

Two values are required. They can be provided in three ways, in order of precedence:

| Priority | Method | Best for |
|---|---|---|
| 1 | **CLI arguments** | One-off runs, switching between environments |
| 2 | **Environment variables** | CI/CD pipelines |
| 3 | **`.env` file** | Local development (avoid retyping credentials) |

### CLI arguments

| Argument | Short | Description |
|---|---|---|
| `--iothub-sas-token` | `-i` | Azure IoT Hub SAS token |
| `--postgres-connection-string` | `-p` | PostgreSQL DSN |
| `--env-file` | | Path to a `.env` file (default: `.env` next to the script) |

Run `uv run scripts/import_devices.py --help` to see all options.

### Environment variables

| Variable | Description |
|---|---|
| `IOTHUB_SAS_TOKEN` | Azure IoT Hub Shared Access Signature token (see below for how to generate one) |
| `POSTGRES_CONNECTION_STRING` | PostgreSQL DSN, e.g. `postgresql://postgres:postgres@127.0.0.1:5433/postgres` |

### `.env` file

Create a `.env` file next to the script in the `scripts/` directory:

```dotenv
IOTHUB_SAS_TOKEN=SharedAccessSignature sr=xxx.azure-devices.net&sig=xxx&se=xxx&skn=iothubowner
POSTGRES_CONNECTION_STRING=postgresql://postgres:postgres@127.0.0.1:5433/postgres
```

> **Note:** use the standard `postgresql://` DSN format. The SQLAlchemy dialect
> prefix `postgresql+psycopg://` will not work here — the script calls
> `psycopg.connect()` directly, which expects a plain libpq connection string.

---

## Generating an IoT Hub SAS token

A SAS token is a time-limited credential. It uses the same `SAS_TOKEN`
convention as the EdgeConfig application itself.

### Via Azure CLI (recommended)

```bash
az iot hub generate-sas-token \
  --hub-name <your-hub-name> \
  --policy-name iothubowner \
  --duration 3600
```

Copy the value of the `"sas"` field from the output — it starts with
`SharedAccessSignature sr=...`.

### Via Azure IoT Explorer (GUI alternative)

[Azure IoT Explorer](https://github.com/Azure/azure-iot-explorer/releases) is a
free Microsoft desktop tool that can generate SAS tokens without the CLI:

1. Open Azure IoT Explorer → connect with your IoT Hub connection string
2. Go to **IoT Hub settings** → **Shared access tokens**
3. Select the `iothubowner` policy, set the desired TTL and copy the token

> **Token expiry:** SAS tokens expire. If the script fails with HTTP 401,
> generate a new token and update your `.env` or environment variable.

---

## Running the script

### Option A — CLI arguments (recommended for local use)

```powershell
uv run scripts/import_devices.py `
  -i "SharedAccessSignature sr=xxx.azure-devices.net&sig=xxx&se=xxx&skn=iothubowner" `
  -p "postgresql://user:password@localhost:5433/dbname"
```

### Option B — `.env` file (convenient for repeated local runs)

```powershell
# Create once, run many times
uv run scripts/import_devices.py
```

### Option C — Environment variables (CI/CD)

**PowerShell**
```powershell
$env:IOTHUB_SAS_TOKEN = "SharedAccessSignature sr=xxx.azure-devices.net&sig=xxx&se=xxx&skn=iothubowner"
$env:POSTGRES_CONNECTION_STRING = "postgresql://user:password@localhost:5433/dbname"

uv run scripts/import_devices.py
```

**Linux / macOS**

```bash
export IOTHUB_SAS_TOKEN="SharedAccessSignature sr=xxx.azure-devices.net&sig=xxx&se=xxx&skn=iothubowner"
export POSTGRES_CONNECTION_STRING="postgresql://user:password@localhost:5433/dbname"

uv run scripts/import_devices.py
```

---

## Expected output

```
2026-03-26 10:00:00 [INFO] Reading allowed meta keys from platform table…
2026-03-26 10:00:00 [INFO] Allowed meta keys (6): ['businessUnit', 'city', 'countryCode', 'customer', 'description', 'geoLocation']
2026-03-26 10:00:00 [INFO] Connecting to IoT Hub…
2026-03-26 10:00:01 [INFO] Querying IoT Edge device twins…
2026-03-26 10:00:02 [INFO] Found 42 IoT Edge device(s).
2026-03-26 10:00:02 [INFO] Connecting to PostgreSQL…
2026-03-26 10:00:02 [INFO]   [inserted] my-edge-device-001
2026-03-26 10:00:02 [INFO]   [updated]  my-edge-device-002
...
2026-03-26 10:00:03 [INFO] Import complete. Inserted: 40 | Updated: 2 | Total: 42
```

---

## Database tables

Both tables are defined in `schema.sql` at the root of the repository. Make
sure they exist before running the script.

```sql
-- verify both tables exist
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('devices', 'platform');

-- inspect the current allow-list
SELECT platform_meta FROM platform WHERE name = 'default';
```

The `platform` table ships with a default seed row (see `schema.sql`). If you
need a different set of importable keys, update `platform_meta` on that row
before running the script:

```sql
UPDATE platform
SET platform_meta = '{"city": null, "customer": null, "myCustomKey": null}'::jsonb
WHERE name = 'default';
```
