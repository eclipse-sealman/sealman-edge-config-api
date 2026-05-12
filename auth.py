import logging
import asyncio
import ssl
import httpx
import jwt
from jwt import PyJWKClient
from authorization.permission_types import Device, Platform
from constants import (
    AUTHENTICATION_PROVIDER,
    AZURE_AD_INSTANCE,
    AZURE_AD_TENANT_ID,
    AZURE_AD_CLIENT_ID,
    AZURE_AD_SCOPES,
    KEYCLOAK_BASE_URL,
    KEYCLOAK_REALM,
    KEYCLOAK_CLIENT_ID,
    ALLOW_INSECURE_HTTPS,
)
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2AuthorizationCodeBearer
from exceptions import InsufficientPermissions, InvalidUserRole
from helper import AuditTrail


class Role:
    EDGE_CONFIG_ADMIN = "user.admin"
    EDGE_CONFIG_EDITOR = "user.editor"
    EDGE_CONFIG_VIEWER = "user.viewer"


PermissionMap = {
    Role.EDGE_CONFIG_ADMIN: Platform.ReadPermissions
        + Platform.EditPermissions
        + Device.ReadPermissions
        + Device.EditPermissions,
    Role.EDGE_CONFIG_VIEWER: Platform.ReadPermissions
        + Device.ReadPermissions
        + [
            Device.EDIT_MODULE_CONFIG_STATUS,
            Device.EXECUTE_MODULE_METHOD,
            Device.DISCOVER_NETWORK,
        ],
    Role.EDGE_CONFIG_EDITOR: Platform.ReadPermissions
        + Platform.EditPermissions
        + Device.ReadPermissions
        + Device.EditPermissions,
}


# Configure authentication endpoints based on provider
if AUTHENTICATION_PROVIDER == "entra":
    _oidc_discovery_url = f"{AZURE_AD_INSTANCE}{AZURE_AD_TENANT_ID}/.well-known/openid-configuration"
    SWAGGER_CLIENT_ID = AZURE_AD_CLIENT_ID
    _oauth2_scopes = {scope: "AAD-User-Access" for scope in AZURE_AD_SCOPES.split(",")} if AZURE_AD_SCOPES else {}
    oauth2_scheme = OAuth2AuthorizationCodeBearer(
        authorizationUrl=f"{AZURE_AD_INSTANCE}{AZURE_AD_TENANT_ID}/oauth2/v2.0/authorize",
        tokenUrl=f"{AZURE_AD_INSTANCE}{AZURE_AD_TENANT_ID}/oauth2/v2.0/token",
        scopes=_oauth2_scopes
    )
elif AUTHENTICATION_PROVIDER == "keycloak":
    _oidc_discovery_url = f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
    SWAGGER_CLIENT_ID = KEYCLOAK_CLIENT_ID
    _oauth2_scopes = {}
    oauth2_scheme = OAuth2AuthorizationCodeBearer(
        authorizationUrl=f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth",
        tokenUrl=f"{KEYCLOAK_BASE_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
        scopes={}
    )

# Lazily initialized in fetch_oidc_issuer()
OIDC_ISSUER: str | None = None
jwks_client: PyJWKClient | None = None


def _build_ssl_context() -> ssl.SSLContext | None:
    if not ALLOW_INSECURE_HTTPS:
        return None

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False  # NOSONAR - intentional, guarded by ALLOW_INSECURE_HTTPS opt-in flag
    ssl_context.verify_mode = ssl.CERT_NONE  # NOSONAR - intentional, guarded by ALLOW_INSECURE_HTTPS opt-in flag
    return ssl_context


async def fetch_oidc_issuer():
    """
    Fetches the OIDC discovery document at startup to resolve the issuer and JWKS URI.
    This avoids hardcoding these values and ensures they always match exactly what the
    provider publishes (e.g. sts.windows.net vs login.microsoftonline.com,
    with or without /v2.0).
    """
    global OIDC_ISSUER, jwks_client
    logger = logging.getLogger("Auth")
    ssl_context = _build_ssl_context()

    if ALLOW_INSECURE_HTTPS:
        logger.warning("ALLOW_INSECURE_HTTPS is enabled. TLS certificate verification is disabled.")

    try:
        async with httpx.AsyncClient(verify=ssl_context or True) as client:
            response = await client.get(_oidc_discovery_url, timeout=10)
            response.raise_for_status()
            discovery = response.json()

        OIDC_ISSUER = discovery["issuer"]
        jwks_client = PyJWKClient(
            discovery["jwks_uri"],
            cache_keys=True,
            lifespan=3600,
            ssl_context=ssl_context,
        )

        logger.info(f"OIDC discovery resolved — issuer: {OIDC_ISSUER}, jwks_uri: {discovery['jwks_uri']}")
    except Exception as ex:
        logger.error(f"Failed to fetch OIDC discovery document: {ex}")
        raise


async def ensure_oidc_initialized() -> None:
    if OIDC_ISSUER is not None and jwks_client is not None:
        return

    await fetch_oidc_issuer()


async def refresh_jwks_cache():
    """
    Background task to proactively fetch and refresh JWKS cache.
    - Fetches immediately on startup to warm the cache
    - Then refreshes every 50 minutes (10 minutes before the 1-hour cache expiration)
    - On failure, retries with exponential backoff (5s, 10s, 20s, 40s, up to 5 minutes)
    This ensures requests never hit an expired cache and have to wait for synchronous key fetching.
    """
    logger = logging.getLogger("Auth")
    # Refresh interval: 50 minutes (10 minutes before cache expires)
    refresh_interval = 3000  # seconds

    # Exponential backoff parameters for retries on failure
    initial_retry_delay = 5  # seconds
    max_retry_delay = 300  # seconds (5 minutes)
    retry_delay = initial_retry_delay

    while True:
        try:
            if jwks_client is None:
                logger.warning("JWKS refresh skipped because OIDC discovery is not initialized yet")
                await asyncio.sleep(retry_delay)
                continue

            # Fetch keys (immediately on first run, then after each sleep interval)
            jwks_client.fetch_data()
            logger.info("JWKS cache refreshed proactively")

            # Reset retry delay on success
            retry_delay = initial_retry_delay

            # Sleep until next refresh
            await asyncio.sleep(refresh_interval)
        except asyncio.CancelledError:
            logger.info("JWKS refresh task cancelled")
            break
        except Exception as ex:
            logger.warning(f"Failed to refresh JWKS cache: {ex}. Will retry in {retry_delay} seconds.")
            # Sleep with exponential backoff before retrying
            await asyncio.sleep(retry_delay)

            # Increase retry delay for next failure (exponential backoff with cap)
            retry_delay = min(retry_delay * 2, max_retry_delay)


# verify token (works for both Entra and KeyCloak)
def verify_token(token: str) -> dict:
    # Set audience based on authentication provider
    if AUTHENTICATION_PROVIDER == "keycloak":
        audience = KEYCLOAK_CLIENT_ID
    else:  # entra
        audience = f"api://{AZURE_AD_CLIENT_ID}"

    # PyJWKClient automatically finds the right key based on the token's kid header
    signing_key = jwks_client.get_signing_key_from_jwt(token)

    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=OIDC_ISSUER,
        options={"verify_exp": True},
    )

async def validate_jwt(token: str = Security(oauth2_scheme)) -> dict:
    """
    Validates JWT token from the configured authentication provider (Entra or KeyCloak).
    """
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        await ensure_oidc_initialized()
        decoded_token = verify_token(token)
        return decoded_token
    except httpx.HTTPError as ex:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"authentication provider unavailable: {ex}",
        )
    except Exception as ex:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=f"invalid token: {ex}")


def get_current_user(auth_context: dict) -> str:
    """
    Extracts a user identifier from the auth context for logging/audit purposes.
    Tries multiple claims to be compatible with different providers and configurations.
    """
    return auth_context.get("name") or auth_context.get("preferred_username") or auth_context.get("email") or auth_context.get("oid") or auth_context.get("sub")


class RBACPermissionChecker:

    def __init__(self, required_permissions: list[str]) -> None:
        self.required_permissions = required_permissions

    async def __call__(self, auth_context: dict = Depends(validate_jwt)) -> dict:
        assigned_permissions = RBACPermissionChecker.get_assigned_permissions(auth_context)

        # check if permission of assigned group fits the required permissions of this call
        for r_perm in self.required_permissions:
            if r_perm not in assigned_permissions:
                raise InsufficientPermissions(f"user has insufficient permissions - required permission: {r_perm}",
                                              status_code=403)
        
        user_id = get_current_user(auth_context)
        org_id = auth_context.get("oid") or auth_context.get("sub")

        await AuditTrail.log(user_id, self.required_permissions)
        return {
            "user_id": user_id,
            "org_id": org_id,
            "assigned_permissions": assigned_permissions,
            "required_permissions": self.required_permissions
        }

    @staticmethod
    def get_assigned_permissions(auth_context: dict) -> list[str]:
        """
        Returns the permissions of the user.
        """
        # get assigned roles of the groups the user is attached to
        assigned_roles = auth_context.get("roles")

        if assigned_roles is None:
            user_id = get_current_user(auth_context)
            raise InvalidUserRole(f"user <{user_id}> is missing required role assignment", status_code=401)

        # collect permissions of assigned groups
        assigned_permissions: list[str] = []
        for role in assigned_roles:
            perm_of_role = PermissionMap.get(role)
            if perm_of_role is not None:
                assigned_permissions.extend(perm_of_role)

        return assigned_permissions
