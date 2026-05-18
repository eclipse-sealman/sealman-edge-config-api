# Authentication
The API supports two authentication providers, configured via the `AUTHENTICATION_PROVIDER` environment variable.

## Providers

### Microsoft Entra ID
Set `AUTHENTICATION_PROVIDER=entra` and configure:

| Variable | Description |
|---|---|
| `AZURE_AD_TENANT_ID` | Entra ID tenant ID |
| `AZURE_AD_CLIENT_ID` | App registration client ID |
| `AZURE_AD_SCOPES` | OAuth scopes, e.g. `api://<client-id>/Edge` |

### Keycloak
Set `AUTHENTICATION_PROVIDER=keycloak` and configure:

| Variable | Description |
|---|---|
| `KEYCLOAK_BASE_URL` | Base URL, e.g. `http://localhost:8080` |
| `KEYCLOAK_REALM` | Realm name |
| `KEYCLOAK_CLIENT_ID` | Public client ID (used by the Swagger UI) |
| `KEYCLOAK_CONFIDENTIAL_CLIENT_ID` | Confidential client ID (used for token validation) |
| `KEYCLOAK_CONFIDENTIAL_CLIENT_SECRET` | Confidential client secret |

## Role-Based Access Control (RBAC)

Roles are read directly from the `roles` claim in the validated JWT. The role-to-permission mapping is defined in `auth.py`.

| Role | Description                                                                                                                                   |
|---|-----------------------------------------------------------------------------------------------------------------------------------------------|
| `user.admin` | Full access — all read and write operations on devices and platform resources                                                                 |
| `user.editor` | Currently same as `user.admin`                                                                                                                |
| `user.viewer` | Read-only access across all resources, plus the ability to execute module methods, update module config status, and trigger network discovery |

Assign one of these roles to users in your identity provider (Entra ID app role assignment or Keycloak role mapping).
