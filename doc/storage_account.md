# Storage Account Prerequisites

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


