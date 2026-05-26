import os
from dotenv import load_dotenv
import base64

load_dotenv()

# Dependencies
LAN_EDGE_TEMPLATE_VERSIONS = []
LAN_EDGE_TEMPLATES = os.getenv("LAN_EDGE_TEMPLATES")
if LAN_EDGE_TEMPLATES is not None:
    lan_edge_templates = [t.strip() for t in LAN_EDGE_TEMPLATES.split(",")]
    LAN_EDGE_TEMPLATE_VERSIONS.extend(lan_edge_templates)

NAT_TEMPLATE_SUPPORT = []
NAT_TEMPLATES = os.getenv("NAT_TEMPLATES")
if NAT_TEMPLATES is not None:
    nat_templates = [t.strip() for t in NAT_TEMPLATES.split(",")]
    NAT_TEMPLATE_SUPPORT.extend(nat_templates)

# AAD login URL
AZURE_AD_INSTANCE = "https://login.microsoftonline.com/"

# Max NAT Rules in SEMS Device Templates
MAX_NAT_ROUTES = 3

# Key for saving device secret information in SEMS
DEVICE_AUTHENTICATION_SECRET_KEY = "admin"

DEFAULT_SEMS_TEMPLATE_BLOB_CONTAINER_NAME = "sems-device-template"
DEFAULT_SEMS_TEMPLATE_BLOB_FILE_NAME = "default-template.json"

# load environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
ROOT_PATH = os.getenv("ROOT_PATH", "")
IOT_HUB_NAME = os.getenv("IOT_HUB_NAME")
SAS_TOKEN = os.getenv("SAS_TOKEN")
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS")
VERSION = os.getenv("VERSION", "local-dev")
ALLOW_INSECURE_HTTPS = os.getenv("ALLOW_INSECURE_HTTPS", "false").lower() == "true"
ALLOW_STARTUP_WITHOUT_OIDC = (
    os.getenv("ALLOW_STARTUP_WITHOUT_OIDC", "false").lower() == "true"
)
# use protocol http or https if provided, else use https
SEMS_URL = os.getenv("SEMS_URL")
if SEMS_URL:
    SEMS_URL = (
        SEMS_URL
        if SEMS_URL.startswith(("http://", "https://"))
        else f"https://{SEMS_URL}"
    )
SEMS_USER = os.getenv("SEMS_USER")
SEMS_PW = os.getenv("SEMS_PW")
SEMS_LOOKUP_INTERVAL = os.getenv("SEMS_LOOKUP_INTERVAL")
DEVICE_CACHE_INTERVAL = int(
    os.getenv("DEVICE_CACHE_INTERVAL", "60")
)  # in seconds, default 1 minute

# Authentication Provider Configuration
_auth_provider = os.getenv("AUTHENTICATION_PROVIDER", "keycloak").lower()
if _auth_provider not in ["entra", "keycloak"]:
    raise ValueError(
        f"Invalid AUTHENTICATION_PROVIDER: '{_auth_provider}'. Valid values are 'Entra' or 'Keycloak'."
    )
AUTHENTICATION_PROVIDER = _auth_provider

# Azure AD / Entra Configuration
AZURE_AD_TENANT_ID = os.getenv("AZURE_AD_TENANT_ID")
AZURE_AD_CLIENT_ID = os.getenv("AZURE_AD_CLIENT_ID")
AZURE_AD_SCOPES = os.getenv("AZURE_AD_SCOPES")

# KeyCloak Configuration
KEYCLOAK_BASE_URL = os.getenv("KEYCLOAK_BASE_URL")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID")
KEYCLOAK_CONFIDENTIAL_CLIENT_ID = os.getenv("KEYCLOAK_CONFIDENTIAL_CLIENT_ID")
KEYCLOAK_CONFIDENTIAL_CLIENT_SECRET = os.getenv("KEYCLOAK_CONFIDENTIAL_CLIENT_SECRET")

PUBLIC_STORAGE_ACCOUNT_NAME = os.getenv("PUBLIC_STORAGE_ACCOUNT_NAME")
INTERNAL_STORAGE_ACCOUNT_NAME = os.getenv("INTERNAL_STORAGE_ACCOUNT_NAME")
BLOB_SAS_TOKEN_MODULE_CONF = os.getenv("BLOB_SAS_TOKEN_MODULE_CONF")
BLOB_SAS_TOKEN_SEMS_TEMPLATE = os.getenv("BLOB_SAS_TOKEN_SEMS_TEMPLATE")
BLOB_SAS_TOKEN_PLATFORM_CONFIG = os.getenv("BLOB_SAS_TOKEN_PLATFORM_CONFIG")
BLOB_SAS_TOKEN_TOPOLOGY = os.getenv("BLOB_SAS_TOKEN_TOPOLOGY")
BLOB_SAS_TOKEN_TEMPLATES = os.getenv("BLOB_SAS_TOKEN_TEMPLATES")

AUTHORIZATION_API_PLATFORM_NAME = "edge_config_app"

# Regex Patterns
DEVICE_SERIAL_NUMBER_PATTERN = r"(?i)^(?:eg-)?(?:fht-)?(?:e)?(\d+)$"
FIRMWARE_VERSION_PATTERN = r"^(\d+)\.(\d+)\.?(\d+)?"

# DATABASE
POSTGRES_URL = os.getenv(
    "POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"
)

# SEMS Bootstrap — idempotent first-time provisioning run at API startup
# Set BOOTSTRAP_ENABLED=true to opt in; disabled by default.
BOOTSTRAP_ENABLED = os.getenv("BOOTSTRAP_ENABLED", "false").lower() == "true"
BOOTSTRAP_SEMS_CONFIG_NAME = os.getenv("BOOTSTRAP_SEMS_CONFIG_NAME", "sealman-config")
BOOTSTRAP_SEMS_TEMPLATE_NAME = os.getenv(
    "BOOTSTRAP_SEMS_TEMPLATE_NAME", "sealman-template"
)
BOOTSTRAP_SEMS_DEVICE_TYPE = os.getenv("BOOTSTRAP_SEMS_DEVICE_TYPE", "Edge gateway")
BOOTSTRAP_SEMS_HEALTH_TIMEOUT = int(os.getenv("BOOTSTRAP_SEMS_HEALTH_TIMEOUT", "120"))

# IoTHub Bootstrap — idempotent creation of the base automatic deployment
BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME = os.getenv(
    "BOOTSTRAP_IOTHUB_DEPLOYMENT_NAME", "seal-base-deployment"
)
BOOTSTRAP_IOTHUB_TARGET_CONDITION = os.getenv(
    "BOOTSTRAP_IOTHUB_TARGET_CONDITION", "tags.deployment='base'"
)
BOOTSTRAP_IOTHUB_PRIORITY = int(os.getenv("BOOTSTRAP_IOTHUB_PRIORITY", "99"))

# Maps SEMS variable name → value from environment.
EDGE_AGENT_IMAGE = os.getenv("EDGE_AGENT_IMAGE", "")
EDGE_HUB_IMAGE = os.getenv("EDGE_HUB_IMAGE", "")
CONTAINER_REGISTRY_ADDRESS = os.getenv("CONTAINER_REGISTRY_ADDRESS", "")
CONTAINER_REGISTRY_USERNAME = os.getenv("CONTAINER_REGISTRY_USERNAME", "")
CONTAINER_REGISTRY_PASSWORD = os.getenv("CONTAINER_REGISTRY_PASSWORD", "")
CONTAINER_REGISTRY_CREDENTIALS_ENCODED = base64.b64encode(
    f"{CONTAINER_REGISTRY_USERNAME}:{CONTAINER_REGISTRY_PASSWORD}".encode()
).decode()
SEMS_DEVICE_URL = os.getenv("SEMS_DEVICE_URL", "")

SEMS_DEVICE_TEMPLATE_VARIABLES = {
    "edge_agent_image": EDGE_AGENT_IMAGE,
    "edge_hub_image": EDGE_HUB_IMAGE,
    "container_registry_address": CONTAINER_REGISTRY_ADDRESS,
    "container_registry_username": CONTAINER_REGISTRY_USERNAME,
    "container_registry_password": CONTAINER_REGISTRY_PASSWORD,
    "container_registry_credentials_encoded": CONTAINER_REGISTRY_CREDENTIALS_ENCODED,
    "sems_device_url": SEMS_DEVICE_URL,
}
