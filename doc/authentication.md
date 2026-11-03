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
| `KEYCLOAK_CLIENT_ID` | Client ID used by the Swagger UI and expected audience of API access tokens |

Configure Keycloak to include `KEYCLOAK_CLIENT_ID` in the access token's `aud`
claim. The API validates JWT signatures using the provider's public signing keys
from OIDC discovery/JWKS, not a client secret.

`KEYCLOAK_CONFIDENTIAL_CLIENT_ID` and `KEYCLOAK_CONFIDENTIAL_CLIENT_SECRET` are
declared in the configuration code but are not used by the current API implementation.

## Authorization: administrator role and ABAC

The API recognizes `user.admin` in the top-level `roles` array of the validated API
access token. Configure the identity provider to emit that claim using the appropriate
Entra ID app-role assignment or Keycloak role mapping.

`auth.ensure_user_exists` synchronizes the database user's `is_admin` flag from that
claim on authenticated requests. Administrators bypass ABAC action/scope checks.
Administrator-only dependencies, such as the one on `GET /{device}/smartems/info`,
require the current token's `user.admin` role directly.

`user.editor` and `user.viewer` do not have a built-in role-to-action mapping in the
current backend. Non-administrator permissions come from database team memberships,
roles, allowed actions and device metadata scopes. Granting platform configuration or
authorization actions does not grant administrator status.

Frontend clients can retrieve their own `is_admin` flag from `GET /auth/user`.
Use `GET /auth/permissions/device?device_id=<id>` for actions evaluated against the
selected device, and `GET /auth/permissions/platform` for global actions.
Identity-provider role changes require a refreshed/reissued API access token.

See [Authorization](authorization.md) for the full team/role/scope model, route
enforcement, effective permissions, and extension key channels. See
[the frontend authorization handoff](authorization-ui-follow-up.md#backend-authentication-and-permission-context)
for the workflow and polling implications.
