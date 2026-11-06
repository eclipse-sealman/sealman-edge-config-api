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

# Device defaults cutover backfill

Run from the repository root using the API's configured `POSTGRES_URL`:

```bash
.venv/bin/python -m scripts.backfill_device_defaults          # dry run, rolls back
.venv/bin/python -m scripts.backfill_device_defaults --apply  # commit explicitly
```

The script fills absent device metadata keys from each device type's current effective
defaults, including default-type fields and type-specific overrides. It preserves every
stored value, including explicit null, and is safe to rerun. Output counts field values,
not distinct devices. Dry run executes the updates transactionally and rolls them back;
both modes briefly lock affected rows. Errors abort the transaction and exit unsuccessfully.
Non-object device metadata (JSON null, arrays, or scalar values) on affected types is
rejected before any fields are filled; the error identifies the device needing repair.
Null field values inside a valid metadata object remain preserved.
It makes no IoT Hub/SEMS calls.

Use this after importing legacy data and before serving devices, preferably while writes
are paused. It preserves current defaults, not unknown historical defaults. This is an
explicit normalization tool, not an automatic migration or a routine default-change job.

# Remove undefined device fields (development/testing)

From the repository root, using the configured `POSTGRES_URL`:

```bash
.venv/bin/python -m scripts.remove_undefined_device_fields --fields test legacy_field --deviceType default
.venv/bin/python -m scripts.remove_undefined_device_fields --all-fields --deviceType default
.venv/bin/python -m scripts.remove_undefined_device_fields --fields test --all-device-types
.venv/bin/python -m scripts.remove_undefined_device_fields --all
```

Both selections must be explicit:

- `--fields <name> [name ...]` or `--all-fields`.
- `--deviceType <type_id>` or `--all-device-types`.
- `--all` is shorthand for `--all-fields --all-device-types` and cannot be combined
  with either selection's flags.

`--deviceType` takes the exact stored type ID (e.g. `default` or a UUID), not its display
label. Unknown IDs fail explicitly rather than reporting zero matches. Old positional
field arguments are no longer accepted; use `--fields` instead. Missing either selection
fails without running cleanup. Names select only those fields, and only on devices whose
effective type does not define them. Any all-selection requires typing `ALL` to continue,
including for dry runs.
Default-type and type-specific fields are always preserved, regardless of their values.
For a field name beginning with `--`, use `--fields=--old-field`.

Dry-run is the default: updates are rolled back. Only `--apply` commits. Output counts
removed field values and affected devices, and states the selected fields/type.
To persist one of the commands above, add `--apply`.
The script changes device metadata only;
type definitions, endpoints, services and external systems are untouched. Custom fields
that were intentionally stored outside the schema will also be removed if selected.
Removing scope metadata may affect access. Use a backed-up development/test database,
preferably with writes paused; the transaction locks type definitions and device rows.
This utility is not part of normal API behavior or startup.
