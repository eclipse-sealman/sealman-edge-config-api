# Storage Account Prerequisites

## Local module configuration with Azurite

Module configuration supports optional `MODULE_CONFIG_STORAGE_URL` (API access) and
`MODULE_CONFIG_DEVICE_STORAGE_URL` (edge access) account endpoints. Leave both unset
for the existing Azure endpoints. The device endpoint defaults to the API endpoint.
Path-style endpoints such as `http://127.0.0.1:10000/opcualab` are supported.
Configure `PUBLIC_STORAGE_ACCOUNT_NAME` and `BLOB_SAS_TOKEN_MODULE_CONF` for that
account and create the private `iotedge-device-twin` container before use.
HTTP is intended only for an isolated local development network.

Both core and extension twin writes use these endpoints and go through one write path
(`routers/module_config/service.py`): every write uploads an immutable blob
`{device}/{module}/{configId}` and records a revision (see `architecture.md`).
Configuration reads follow the twin's configured blob within that device/module
directory. Devices without a pointer, or written by older API versions, retain the
legacy `current` lookup. Blobs are never deleted; plan a lifecycle rule if the
history should be bounded.
Other storage features, including binary configuration and SEMS templates, continue
to use their existing storage settings.

The workspace's `opcua-lab` compose project includes an emulator and initialization
script. Never reuse emulator credentials for production.

## Platform Configuration Bootstrap

Before using platform configuration features, the following Azure Blob Storage resources must be created manually in the storage account specified by the `INTERNAL_STORAGE_ACCOUNT_NAME` environment variable.

### Prerequisites

- Environment variable `INTERNAL_STORAGE_ACCOUNT_NAME` must be set with the name of the Azure Storage Account (no suffix like `.blob.core.windows.net`)
- Environment variable `BLOB_SAS_TOKEN_IOT_PLATFORM_CONFIGURATION` must be set with a SAS token that has read and write permissions to the container

### Manual Setup

Container: `platform-config` \
Blobs required in that container:
  - `services.json` and `endpoint-types.json` with initial content:

```json
[]
```

- `templates.json` with initial content:

```json
{
  "selected": []
}
```

If these blobs are missing, platform configuration endpoints may fail when reading initial values.

## Note

This bootstrap is currently a manual setup step. In the future, this data is planned to be moved from Blob Storage to PostgreSQL.

