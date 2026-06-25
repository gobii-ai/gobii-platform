import json
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.models import GlobalSecret, MCPServerConfig, PersistentAgent
from api.services.pipedream_apps import disable_pipedream_apps_for_owner
from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent

logger = logging.getLogger(__name__)

NATIVE_INTEGRATION_SECRET_PREFIX = "native_"
TOKEN_REFRESH_SKEW = timedelta(minutes=5)
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"


class NativeIntegrationError(Exception):
    """Base error for native integration failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        provider_key: str = "",
        provider_name: str = "",
        setup_url: str = "",
        missing_scopes: list[str] | tuple[str, ...] | None = None,
        granted_scopes: list[str] | tuple[str, ...] | None = None,
        requested_scopes: list[str] | tuple[str, ...] | None = None,
        retryable: bool | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.provider_key = provider_key
        self.provider_name = provider_name
        self.setup_url = setup_url
        self.missing_scopes = list(missing_scopes or [])
        self.granted_scopes = list(granted_scopes or [])
        self.requested_scopes = list(requested_scopes or [])
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.code:
            payload["code"] = self.code
        if self.provider_key:
            payload["provider_key"] = self.provider_key
        if self.provider_name:
            payload["provider_name"] = self.provider_name
        if self.setup_url:
            payload["setup_url"] = self.setup_url
        if self.missing_scopes:
            payload["missing_scopes"] = self.missing_scopes
        if self.granted_scopes:
            payload["granted_scopes"] = self.granted_scopes
        if self.requested_scopes:
            payload["requested_scopes"] = self.requested_scopes
        if self.retryable is not None:
            payload["retryable"] = self.retryable
        return payload


class NativeIntegrationConfigurationError(NativeIntegrationError):
    """Raised when a provider is not configured on this deployment."""


class NativeIntegrationAuthError(NativeIntegrationError):
    """Raised when a stored integration cannot authenticate a request."""


class NativeIntegrationTokenRequestError(NativeIntegrationAuthError):
    """Raised when an OAuth token endpoint request fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        response_body: str = "",
        detail: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.detail = detail


class NativeIntegrationFileListError(NativeIntegrationAuthError):
    """Raised when an accessible-file list cannot be loaded from a provider."""

    def __init__(self, message: str, *, status_code: int = 502, detail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class NativeIntegrationProvider:
    key: str
    display_name: str
    description: str
    auth_type: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: tuple[str, ...]
    api_hosts: tuple[str, ...]
    api_url_prefixes: tuple[str, ...]
    icon: str
    authorization_params: dict[str, str]

    @property
    def secret_key(self) -> str:
        return f"{NATIVE_INTEGRATION_SECRET_PREFIX}{self.key}"

    @property
    def scope_string(self) -> str:
        return " ".join(self.scopes)


@dataclass(frozen=True)
class NativeIntegrationCapability:
    key: str
    provider_key: str
    resource: str
    operation: str
    label: str
    required_scopes: tuple[str, ...]
    endpoint_hints: tuple[str, ...]
    write_risk: str
    setup_guidance: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "provider_key": self.provider_key,
            "resource": self.resource,
            "operation": self.operation,
            "label": self.label,
            "required_scopes": list(self.required_scopes),
            "endpoint_hints": list(self.endpoint_hints),
            "write_risk": self.write_risk,
            "setup_guidance": self.setup_guidance,
        }


@dataclass(frozen=True)
class NativeIntegrationAccessibleFile:
    external_id: str
    name: str
    mime_type: str
    web_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "external_id": self.external_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "web_url": self.web_url,
        }


GOOGLE_DRIVE_PROVIDER = NativeIntegrationProvider(
    key="google_drive",
    display_name="Google Drive",
    description="Grant file access for Google Sheets and Google Docs.",
    auth_type="oauth2",
    authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    scopes=("https://www.googleapis.com/auth/drive.file",),
    api_hosts=("sheets.googleapis.com", "docs.googleapis.com", "drive.googleapis.com"),
    api_url_prefixes=("https://www.googleapis.com/drive/",),
    icon="google_drive",
    authorization_params={
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent",
    },
)

APOLLO_PROVIDER = NativeIntegrationProvider(
    key="apollo",
    display_name="Apollo",
    description="Connect Apollo for lead sourcing, enrichment, CRM, sequencing, analytics, and sales intelligence APIs.",
    auth_type="oauth2",
    authorization_endpoint="https://app.apollo.io/#/oauth/authorize",
    token_endpoint="https://app.apollo.io/api/v1/oauth/token",
    scopes=tuple(settings.APOLLO_OAUTH_SCOPES),
    api_hosts=(),
    api_url_prefixes=(
        "https://api.apollo.io/",
        "https://app.apollo.io/api/v1/users/api_profile",
    ),
    icon="apollo",
    authorization_params={},
)

HUBSPOT_PROVIDER = NativeIntegrationProvider(
    key="hubspot",
    display_name="HubSpot",
    description="Connect HubSpot for contacts, companies, deals, owners, properties, and CRM workflows.",
    auth_type="oauth2",
    authorization_endpoint="https://app.hubspot.com/oauth/authorize",
    token_endpoint="https://api.hubapi.com/oauth/v3/token",
    scopes=tuple(settings.HUBSPOT_OAUTH_SCOPES),
    api_hosts=(),
    api_url_prefixes=("https://api.hubapi.com/",),
    icon="hubspot",
    authorization_params={},
)

SLACK_PROVIDER = NativeIntegrationProvider(
    key="slack",
    display_name="Slack",
    description="Connect Slack so agents can subscribe to channels, receive messages, and reply with display-level agent identity.",
    auth_type="oauth2",
    authorization_endpoint="https://slack.com/oauth/v2/authorize",
    token_endpoint="https://slack.com/api/oauth.v2.access",
    scopes=tuple(settings.SLACK_OAUTH_SCOPES),
    api_hosts=("slack.com",),
    api_url_prefixes=("https://slack.com/api/",),
    icon="slack",
    authorization_params={},
)

GOOGLE_SHEETS_PROVIDER = GOOGLE_DRIVE_PROVIDER
GOOGLE_DRIVE_PROVIDER_ALIASES = ("google_sheets",)
GOOGLE_DRIVE_LEGACY_SECRET_KEYS = ("native_google_sheets",)

NATIVE_INTEGRATION_PROVIDERS = {
    GOOGLE_DRIVE_PROVIDER.key: GOOGLE_DRIVE_PROVIDER,
    APOLLO_PROVIDER.key: APOLLO_PROVIDER,
    HUBSPOT_PROVIDER.key: HUBSPOT_PROVIDER,
    SLACK_PROVIDER.key: SLACK_PROVIDER,
}

NATIVE_INTEGRATION_CAPABILITIES: dict[str, tuple[NativeIntegrationCapability, ...]] = {
    GOOGLE_DRIVE_PROVIDER.key: (
        NativeIntegrationCapability(
            key="google_drive_file_discovery",
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            resource="drive_files",
            operation="list",
            label="List selected Google Drive spreadsheets and docs",
            required_scopes=("https://www.googleapis.com/auth/drive.file",),
            endpoint_hints=("GET https://www.googleapis.com/drive/v3/files",),
            write_risk="read",
            setup_guidance="Connect Google Drive and choose the relevant file in Google Picker.",
        ),
        NativeIntegrationCapability(
            key="google_sheets_read",
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            resource="google_sheets",
            operation="read",
            label="Read selected Google Sheets metadata and values",
            required_scopes=("https://www.googleapis.com/auth/drive.file",),
            endpoint_hints=(
                "GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}",
                "GET https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}",
            ),
            write_risk="read",
            setup_guidance="Connect Google Drive and choose the spreadsheet in Google Picker.",
        ),
        NativeIntegrationCapability(
            key="google_sheets_write",
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            resource="google_sheets",
            operation="write",
            label="Update, append, and edit values in selected Google Sheets",
            required_scopes=("https://www.googleapis.com/auth/drive.file",),
            endpoint_hints=(
                "PUT https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}",
                "POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}/values/{range}:append",
            ),
            write_risk="write",
            setup_guidance="Connect Google Drive and choose the spreadsheet in Google Picker.",
        ),
        NativeIntegrationCapability(
            key="google_sheets_create",
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            resource="google_sheets",
            operation="create",
            label="Create new Google Sheets spreadsheets",
            required_scopes=("https://www.googleapis.com/auth/drive.file",),
            endpoint_hints=("POST https://sheets.googleapis.com/v4/spreadsheets",),
            write_risk="write",
            setup_guidance="Connect Google Drive before creating spreadsheets.",
        ),
        NativeIntegrationCapability(
            key="google_sheets_format",
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            resource="google_sheets",
            operation="format",
            label="Format selected Google Sheets with styles, banding, frozen rows, and column sizing",
            required_scopes=("https://www.googleapis.com/auth/drive.file",),
            endpoint_hints=("POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}:batchUpdate",),
            write_risk="write",
            setup_guidance="Connect Google Drive and choose the spreadsheet in Google Picker.",
        ),
        NativeIntegrationCapability(
            key="google_sheets_chart",
            provider_key=GOOGLE_DRIVE_PROVIDER.key,
            resource="google_sheets",
            operation="chart",
            label="Create and update charts in selected Google Sheets",
            required_scopes=("https://www.googleapis.com/auth/drive.file",),
            endpoint_hints=("POST https://sheets.googleapis.com/v4/spreadsheets/{spreadsheetId}:batchUpdate",),
            write_risk="write",
            setup_guidance="Connect Google Drive and choose the spreadsheet in Google Picker.",
        ),
    ),
    APOLLO_PROVIDER.key: (
        NativeIntegrationCapability(
            key="apollo_people_search",
            provider_key=APOLLO_PROVIDER.key,
            resource="people",
            operation="search",
            label="Search Apollo people",
            required_scopes=("mixed_people_api_search",),
            endpoint_hints=("POST https://api.apollo.io/api/v1/mixed_people/api_search",),
            write_risk="read",
        ),
        NativeIntegrationCapability(
            key="apollo_company_search",
            provider_key=APOLLO_PROVIDER.key,
            resource="companies",
            operation="search",
            label="Search Apollo organizations",
            required_scopes=("mixed_companies_search",),
            endpoint_hints=("POST https://api.apollo.io/api/v1/mixed_companies/search",),
            write_risk="read",
        ),
        NativeIntegrationCapability(
            key="apollo_people_enrich",
            provider_key=APOLLO_PROVIDER.key,
            resource="people",
            operation="enrich",
            label="Enrich Apollo people",
            required_scopes=("people_match",),
            endpoint_hints=(
                "POST https://api.apollo.io/api/v1/people/match",
                "POST https://api.apollo.io/api/v1/people/bulk_match",
            ),
            write_risk="sensitive",
            setup_guidance="Reconnect Apollo if enrichment scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="apollo_contacts_write",
            provider_key=APOLLO_PROVIDER.key,
            resource="contacts",
            operation="write",
            label="Create or update Apollo contacts",
            required_scopes=("contact_write", "contact_update"),
            endpoint_hints=("POST/PATCH https://api.apollo.io/api/v1/contacts",),
            write_risk="write",
            setup_guidance="Reconnect Apollo if contact write scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="apollo_usage_read",
            provider_key=APOLLO_PROVIDER.key,
            resource="usage",
            operation="read",
            label="Read Apollo API and credit usage",
            required_scopes=("api_usage_stats_read", "credit_usage_stats_read"),
            endpoint_hints=("GET https://api.apollo.io/api/v1/usage_stats",),
            write_risk="read",
        ),
    ),
    HUBSPOT_PROVIDER.key: (
        NativeIntegrationCapability(
            key="hubspot_contacts_read",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="contacts",
            operation="read",
            label="Search and read HubSpot contacts",
            required_scopes=("crm.objects.contacts.read",),
            endpoint_hints=("POST https://api.hubapi.com/crm/v3/objects/contacts/search",),
            write_risk="read",
        ),
        NativeIntegrationCapability(
            key="hubspot_contacts_write",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="contacts",
            operation="write",
            label="Create or update HubSpot contacts",
            required_scopes=("crm.objects.contacts.write",),
            endpoint_hints=("POST/PATCH https://api.hubapi.com/crm/v3/objects/contacts",),
            write_risk="write",
            setup_guidance="Reconnect HubSpot if contact write scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="hubspot_companies_read",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="companies",
            operation="read",
            label="Search and read HubSpot companies",
            required_scopes=("crm.objects.companies.read",),
            endpoint_hints=("POST https://api.hubapi.com/crm/v3/objects/companies/search",),
            write_risk="read",
        ),
        NativeIntegrationCapability(
            key="hubspot_companies_write",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="companies",
            operation="write",
            label="Create or update HubSpot companies",
            required_scopes=("crm.objects.companies.write",),
            endpoint_hints=("POST/PATCH https://api.hubapi.com/crm/v3/objects/companies",),
            write_risk="write",
            setup_guidance="Reconnect HubSpot if company write scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="hubspot_deals_read",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="deals",
            operation="read",
            label="Search and read HubSpot deals",
            required_scopes=("crm.objects.deals.read",),
            endpoint_hints=("POST https://api.hubapi.com/crm/v3/objects/deals/search",),
            write_risk="read",
        ),
        NativeIntegrationCapability(
            key="hubspot_deals_write",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="deals",
            operation="write",
            label="Create or update HubSpot deals",
            required_scopes=("crm.objects.deals.write",),
            endpoint_hints=("POST/PATCH https://api.hubapi.com/crm/v3/objects/deals",),
            write_risk="write",
            setup_guidance="Reconnect HubSpot if deal write scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="hubspot_metadata_read",
            provider_key=HUBSPOT_PROVIDER.key,
            resource="metadata",
            operation="read",
            label="Read HubSpot owners and CRM properties",
            required_scopes=(
                "crm.objects.owners.read",
                "crm.schemas.contacts.read",
                "crm.schemas.companies.read",
                "crm.schemas.deals.read",
            ),
            endpoint_hints=(
                "GET https://api.hubapi.com/crm/v3/owners/",
                "GET https://api.hubapi.com/crm/v3/properties/{objectType}",
            ),
            write_risk="read",
        ),
    ),
    SLACK_PROVIDER.key: (
        NativeIntegrationCapability(
            key="slack_channel_discovery",
            provider_key=SLACK_PROVIDER.key,
            resource="conversations",
            operation="list",
            label="List accessible Slack public and private channels",
            required_scopes=("channels:read", "groups:read"),
            endpoint_hints=("GET https://slack.com/api/conversations.list",),
            write_risk="read",
            setup_guidance="Reconnect Slack if channel discovery scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="slack_channel_messages_read",
            provider_key=SLACK_PROVIDER.key,
            resource="messages",
            operation="read",
            label="Receive Slack channel messages for subscribed channels",
            required_scopes=("channels:history", "groups:history", "users:read"),
            endpoint_hints=(
                "Slack Events API message.channels and message.groups callbacks",
                "GET https://slack.com/api/users.info",
            ),
            write_risk="read",
            setup_guidance="Reconnect Slack if channel history or user profile scopes are missing.",
        ),
        NativeIntegrationCapability(
            key="slack_messages_send",
            provider_key=SLACK_PROVIDER.key,
            resource="messages",
            operation="write",
            label="Send Slack messages with this agent's display name",
            required_scopes=("chat:write", "chat:write.customize"),
            endpoint_hints=("POST https://slack.com/api/chat.postMessage",),
            write_risk="write",
            setup_guidance="Reconnect Slack if customized message posting scopes are missing.",
        ),
    ),
}

NATIVE_INTEGRATION_PIPEDREAM_APP_SLUGS = {
    GOOGLE_DRIVE_PROVIDER.key: ("google_sheets", "google_drive"),
    APOLLO_PROVIDER.key: ("apollo_io", "apollo_io_oauth"),
    HUBSPOT_PROVIDER.key: ("hubspot",),
    SLACK_PROVIDER.key: ("slack",),
}


def list_native_integration_providers() -> list[NativeIntegrationProvider]:
    return list(NATIVE_INTEGRATION_PROVIDERS.values())


def list_native_integration_capabilities(provider_key: str) -> list[NativeIntegrationCapability]:
    provider = get_native_integration_provider(provider_key)
    return list(NATIVE_INTEGRATION_CAPABILITIES.get(provider.key, ()))


def get_native_integration_capability(provider_key: str, capability_key: str) -> NativeIntegrationCapability:
    normalized_key = str(capability_key or "").strip()
    for capability in list_native_integration_capabilities(provider_key):
        if capability.key == normalized_key:
            return capability
    raise KeyError(capability_key)


def get_native_integration_provider(provider_key: str) -> NativeIntegrationProvider:
    normalized_key = str(provider_key or "").strip()
    if normalized_key in GOOGLE_DRIVE_PROVIDER_ALIASES:
        normalized_key = GOOGLE_DRIVE_PROVIDER.key
    provider = NATIVE_INTEGRATION_PROVIDERS.get(normalized_key)
    if provider is None:
        raise KeyError(provider_key)
    return provider


def native_integration_client_credentials(provider: NativeIntegrationProvider) -> tuple[str, str]:
    if provider.key == GOOGLE_DRIVE_PROVIDER.key:
        return settings.GOOGLE_DRIVE_CLIENT_ID, settings.GOOGLE_DRIVE_CLIENT_SECRET
    if provider.key == APOLLO_PROVIDER.key:
        return settings.APOLLO_CLIENT_ID, settings.APOLLO_CLIENT_SECRET
    if provider.key == HUBSPOT_PROVIDER.key:
        return settings.HUBSPOT_CLIENT_ID, settings.HUBSPOT_CLIENT_SECRET
    if provider.key == SLACK_PROVIDER.key:
        return settings.SLACK_CLIENT_ID, settings.SLACK_CLIENT_SECRET
    return "", ""


def native_integration_secret_queryset(owner_user, owner_org):
    if owner_org is not None:
        return GlobalSecret.objects.filter(
            organization=owner_org,
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
        )
    return GlobalSecret.objects.filter(
        user=owner_user,
        organization__isnull=True,
        secret_type=GlobalSecret.SecretType.INTEGRATION,
        domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
    )


def disable_overlapping_pipedream_tools_for_native_integration(
    provider_key: str,
    owner_user,
    owner_org,
) -> list[str]:
    provider = get_native_integration_provider(provider_key)
    app_slugs = NATIVE_INTEGRATION_PIPEDREAM_APP_SLUGS.get(provider.key, ())
    if not app_slugs:
        return []

    if owner_org is not None:
        owner_scope = MCPServerConfig.Scope.ORGANIZATION
    else:
        owner_scope = MCPServerConfig.Scope.USER
    result = disable_pipedream_apps_for_owner(
        owner_scope,
        app_slugs,
        owner_user=owner_user,
        owner_org=owner_org,
    )
    return result["disabled_tools"]


def _native_integration_secret_keys(provider: NativeIntegrationProvider) -> list[str]:
    keys = [provider.secret_key]
    if provider.key == GOOGLE_DRIVE_PROVIDER.key:
        keys.extend(GOOGLE_DRIVE_LEGACY_SECRET_KEYS)
    return keys


def _native_integration_not_connected_guidance(provider: NativeIntegrationProvider) -> str:
    setup_url = native_integration_setup_url()
    if provider.key == GOOGLE_DRIVE_PROVIDER.key:
        return (
            f"Ask the user to open {setup_url}, connect Google Drive, "
            "and choose the relevant file."
        )
    if provider.key == SLACK_PROVIDER.key:
        return f"Ask the user to open {setup_url}, connect Slack, and choose Slack channels for this agent."
    return f"Ask the user to open {setup_url} and connect {provider.display_name}."


def parse_native_integration_scopes(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        raw_scopes = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        raw_scopes = []
        for item in value:
            raw_scopes.extend(parse_native_integration_scopes(item))
    else:
        raw_scopes = str(value).replace(",", " ").split()

    seen: set[str] = set()
    scopes: list[str] = []
    for raw_scope in raw_scopes:
        scope = str(raw_scope or "").strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        scopes.append(scope)
    return tuple(scopes)


def _credentials_granted_scopes(credentials: dict[str, Any] | None, provider: NativeIntegrationProvider) -> tuple[str, ...]:
    if not credentials:
        return ()
    granted_scopes = parse_native_integration_scopes(credentials.get("scope"))
    if not granted_scopes:
        granted_scopes = parse_native_integration_scopes(credentials.get("scopes"))
    if not granted_scopes:
        metadata = credentials.get("metadata")
        if isinstance(metadata, dict):
            granted_scopes = parse_native_integration_scopes(metadata.get("scopes"))
    return granted_scopes or provider.scopes


def _capability_status(capability: NativeIntegrationCapability, connected: bool, granted_scopes: tuple[str, ...]) -> dict[str, Any]:
    granted_set = set(granted_scopes)
    missing_scopes = [
        scope
        for scope in capability.required_scopes
        if scope not in granted_set
    ]
    available = bool(connected and not missing_scopes)
    return {
        **capability.to_dict(),
        "available": available,
        "missing_scopes": missing_scopes,
    }


def build_native_integration_permission_summary(
    provider: NativeIntegrationProvider,
    credentials: dict[str, Any] | None = None,
    *,
    connected: bool | None = None,
) -> dict[str, Any]:
    is_connected = bool(credentials) if connected is None else bool(connected)
    granted_scopes = _credentials_granted_scopes(credentials, provider) if is_connected else ()
    requested_scopes = parse_native_integration_scopes(provider.scopes)
    capability_statuses = [
        _capability_status(capability, is_connected, granted_scopes)
        for capability in list_native_integration_capabilities(provider.key)
    ]
    available = [capability for capability in capability_statuses if capability["available"]]
    missing = [capability for capability in capability_statuses if not capability["available"]]
    missing_scopes = sorted(
        {
            scope
            for capability in missing
            for scope in capability.get("missing_scopes", [])
        }
    )

    if not is_connected:
        status_text = (
            f"{provider.display_name} is not connected. "
            f"{_native_integration_not_connected_guidance(provider)}"
        )
    elif missing_scopes:
        status_text = (
            f"{provider.display_name} is connected, but some capabilities need additional scopes: "
            f"{', '.join(missing_scopes)}."
        )
    else:
        labels = [str(capability["label"]) for capability in available]
        status_text = f"{provider.display_name} is connected with access for: {', '.join(labels)}."

    return {
        "provider_key": provider.key,
        "provider_name": provider.display_name,
        "connected": is_connected,
        "setup_url": native_integration_setup_url(),
        "requested_scopes": list(requested_scopes),
        "granted_scopes": list(granted_scopes),
        "granted_scope_string": " ".join(granted_scopes),
        "available_capabilities": available,
        "missing_capabilities": missing,
        "missing_scopes": missing_scopes,
        "status_text": status_text,
    }


def format_native_integration_permission_prompt(
    provider_key: str,
    owner_user,
    owner_org,
    *,
    max_capabilities: int = 6,
) -> str:
    provider = get_native_integration_provider(provider_key)
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    credentials: dict[str, Any] | None = None
    if secret is not None:
        try:
            credentials = load_native_integration_credentials(secret)
        except NativeIntegrationAuthError:
            credentials = None
    summary = build_native_integration_permission_summary(
        provider,
        credentials,
        connected=secret is not None and credentials is not None,
    )
    if not summary["connected"]:
        prompt_status = f"{provider.display_name} is not connected."
    elif summary["missing_scopes"]:
        prompt_status = f"{provider.display_name} is connected, but some capabilities need additional scopes."
    else:
        prompt_status = f"{provider.display_name} is connected."

    lines = [
        "Native integration permissions:",
        f"- Status: {prompt_status}",
    ]
    available_labels = [capability["label"] for capability in summary["available_capabilities"][:max_capabilities]]
    if available_labels:
        lines.append(f"- Available capabilities: {', '.join(available_labels)}")
    missing_capabilities = summary["missing_capabilities"]
    if missing_capabilities:
        missing_labels = [capability["label"] for capability in missing_capabilities[:max_capabilities]]
        lines.append(f"- Not currently available: {', '.join(missing_labels)}")
    if summary["granted_scopes"]:
        lines.append(f"- Granted scopes: {', '.join(summary['granted_scopes'])}")
    if summary["missing_scopes"]:
        lines.append(f"- Missing scopes: {', '.join(summary['missing_scopes'])}")
    if not summary["connected"]:
        lines.append(f"- Setup: {_native_integration_not_connected_guidance(provider)}")
    return "\n".join(lines)


def native_integration_is_connected(
    provider_key: str,
    owner_user,
    owner_org,
) -> bool:
    provider = get_native_integration_provider(provider_key)
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    return secret is not None


def preflight_native_integration_capability(
    agent: PersistentAgent,
    provider_key: str,
    capability_key: str,
) -> dict[str, Any]:
    provider = get_native_integration_provider(provider_key)
    capability = get_native_integration_capability(provider.key, capability_key)
    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    credentials: dict[str, Any] | None = None
    if secret is not None:
        try:
            credentials = load_native_integration_credentials(secret)
        except NativeIntegrationAuthError:
            credentials = None

    summary = build_native_integration_permission_summary(
        provider,
        credentials,
        connected=secret is not None and credentials is not None,
    )
    granted_scopes = tuple(summary["granted_scopes"])
    capability_summary = _capability_status(capability, bool(summary["connected"]), granted_scopes)
    allowed = bool(capability_summary["available"])
    if allowed:
        next_action = f"Use `http_request` for {capability.label}."
    elif not summary["connected"]:
        next_action = _native_integration_not_connected_guidance(provider)
    else:
        next_action = (
            f"Reconnect {provider.display_name} from {native_integration_setup_url()} "
            f"to grant: {', '.join(capability_summary['missing_scopes'])}."
        )

    return {
        "provider_key": provider.key,
        "provider_name": provider.display_name,
        "capability": capability_summary,
        "connected": summary["connected"],
        "allowed": allowed,
        "granted_scopes": summary["granted_scopes"],
        "requested_scopes": summary["requested_scopes"],
        "missing_scopes": capability_summary["missing_scopes"],
        "setup_url": summary["setup_url"],
        "recommended_next_action": next_action,
    }


def _native_integration_error_kwargs(
    provider: NativeIntegrationProvider,
    *,
    code: str,
    credentials: dict[str, Any] | None = None,
    missing_scopes: list[str] | tuple[str, ...] | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    granted_scopes = _credentials_granted_scopes(credentials, provider)
    return {
        "code": code,
        "provider_key": provider.key,
        "provider_name": provider.display_name,
        "setup_url": native_integration_setup_url(),
        "missing_scopes": list(missing_scopes or []),
        "granted_scopes": list(granted_scopes),
        "requested_scopes": list(parse_native_integration_scopes(provider.scopes)),
        "retryable": retryable,
    }


def get_native_integration_secret(provider_key: str, owner_user, owner_org) -> GlobalSecret | None:
    provider = get_native_integration_provider(provider_key)
    queryset = native_integration_secret_queryset(owner_user, owner_org)
    for key in _native_integration_secret_keys(provider):
        secret = queryset.filter(key=key).first()
        if secret is not None:
            return secret
    return None


def load_native_integration_credentials(secret: GlobalSecret) -> dict[str, Any]:
    raw_value = secret.get_value()
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise NativeIntegrationAuthError("Stored integration credentials are invalid. Reconnect the app.") from exc
    if not isinstance(payload, dict):
        raise NativeIntegrationAuthError("Stored integration credentials are invalid. Reconnect the app.")
    return payload


def save_native_integration_credentials(
    provider: NativeIntegrationProvider,
    owner_user,
    owner_org,
    credentials: dict[str, Any],
) -> GlobalSecret:
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    if secret is None:
        secret = GlobalSecret(
            user=owner_user,
            organization=owner_org,
            name=provider.display_name,
            description=provider.description,
            secret_type=GlobalSecret.SecretType.INTEGRATION,
            domain_pattern=GlobalSecret.INTEGRATION_DOMAIN_SENTINEL,
            key=provider.secret_key,
        )
    else:
        secret.name = provider.display_name
        secret.description = provider.description
        secret.key = provider.secret_key

    secret.set_value(json.dumps(credentials, separators=(",", ":"), sort_keys=True))
    secret.save()
    return secret


def delete_native_integration_credentials(provider_key: str, owner_user, owner_org) -> bool:
    provider = get_native_integration_provider(provider_key)
    deleted_count, _ = native_integration_secret_queryset(owner_user, owner_org).filter(
        key__in=_native_integration_secret_keys(provider),
    ).delete()
    return deleted_count > 0


def build_oauth_credentials_bundle(
    provider: NativeIntegrationProvider,
    token_payload: dict[str, Any],
    *,
    existing_credentials: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        raise ValidationError({"access_token": "Token response missing access_token."})

    refresh_token = token_payload.get("refresh_token") or (existing_credentials or {}).get("refresh_token") or ""
    expires_at = None
    expires_in = token_payload.get("expires_in")
    if expires_in is not None:
        try:
            expires_seconds = int(expires_in)
            expires_at = (timezone.now() + timedelta(seconds=max(expires_seconds, 0))).isoformat()
        except (TypeError, ValueError):
            expires_at = None

    scope = token_payload.get("scope")
    if not scope and isinstance(token_payload.get("scopes"), list):
        scope = " ".join(str(item) for item in token_payload.get("scopes") if item)

    return {
        "provider_key": provider.key,
        "auth_type": provider.auth_type,
        "access_token": access_token,
        "refresh_token": str(refresh_token or ""),
        "token_type": str(token_payload.get("token_type") or "Bearer"),
        "scope": str(scope or provider.scope_string),
        "expires_at": expires_at,
        "metadata": {
            "api_hosts": list(provider.api_hosts),
            "api_url_prefixes": list(provider.api_url_prefixes),
            "scopes": list(provider.scopes),
            "last_token_response": {
                key: value
                for key, value in token_payload.items()
                if key not in {"access_token", "refresh_token", "id_token"}
            },
        },
    }


def request_oauth_token(
    provider: NativeIntegrationProvider,
    data: dict[str, Any],
    *,
    request_error_message: str,
    endpoint_error_message: str,
    invalid_json_message: str,
) -> dict[str, Any]:
    try:
        response = httpx.post(provider.token_endpoint, data=data, timeout=15.0)
    except httpx.HTTPError as exc:
        raise NativeIntegrationTokenRequestError(
            request_error_message,
            status_code=502,
            detail=str(exc),
        ) from exc

    if response.status_code >= 400:
        raise NativeIntegrationTokenRequestError(
            endpoint_error_message,
            status_code=response.status_code,
            response_body=response.text,
        )

    try:
        token_payload = response.json()
    except ValueError as exc:
        raise NativeIntegrationTokenRequestError(invalid_json_message, status_code=502) from exc
    if not isinstance(token_payload, dict):
        raise NativeIntegrationTokenRequestError(invalid_json_message, status_code=502)
    return token_payload


def provider_matches_url(provider: NativeIntegrationProvider, url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    normalized_path = parsed.path or "/"
    normalized_url = f"{parsed.scheme.lower()}://{host}{normalized_path}"
    for allowed_prefix in provider.api_url_prefixes:
        normalized_prefix = allowed_prefix.lower()
        if normalized_url == normalized_prefix.rstrip("/") or normalized_url.startswith(normalized_prefix):
            return True
    for allowed_host in provider.api_hosts:
        normalized_allowed = allowed_host.lower()
        if host == normalized_allowed or host.endswith(f".{normalized_allowed}"):
            return True
    return False


def find_provider_for_url(url: str) -> NativeIntegrationProvider | None:
    for provider in list_native_integration_providers():
        if provider_matches_url(provider, url):
            return provider
    return None


def native_integration_capability_for_request(
    provider: NativeIntegrationProvider,
    url: str,
    method: str = "GET",
) -> NativeIntegrationCapability | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    normalized_method = str(method or "GET").strip().upper()

    capability_key = ""
    if provider.key == GOOGLE_DRIVE_PROVIDER.key:
        if host == "sheets.googleapis.com":
            if normalized_method == "POST" and path.rstrip("/") == "/v4/spreadsheets":
                capability_key = "google_sheets_create"
            elif normalized_method == "POST" and path.endswith(":batchupdate"):
                capability_key = "google_sheets_format"
            elif normalized_method in {"POST", "PUT", "PATCH", "DELETE"}:
                capability_key = "google_sheets_write"
            else:
                capability_key = "google_sheets_read"
        elif host == "www.googleapis.com" and path.startswith("/drive/"):
            capability_key = "google_drive_file_discovery"
        elif host in {"drive.googleapis.com", "docs.googleapis.com"}:
            capability_key = "google_drive_file_discovery"
    elif provider.key == APOLLO_PROVIDER.key:
        if "/mixed_people/api_search" in path:
            capability_key = "apollo_people_search"
        elif "/mixed_companies/search" in path:
            capability_key = "apollo_company_search"
        elif "/people/match" in path or "/people/bulk_match" in path:
            capability_key = "apollo_people_enrich"
        elif "/contacts" in path and normalized_method in {"POST", "PUT", "PATCH", "DELETE"}:
            capability_key = "apollo_contacts_write"
        elif "usage" in path:
            capability_key = "apollo_usage_read"
    elif provider.key == HUBSPOT_PROVIDER.key:
        is_write = normalized_method in {"POST", "PUT", "PATCH", "DELETE"}
        is_search = path.endswith("/search")
        if "/crm/v3/objects/contacts" in path:
            capability_key = "hubspot_contacts_read" if is_search or not is_write else "hubspot_contacts_write"
        elif "/crm/v3/objects/companies" in path:
            capability_key = "hubspot_companies_read" if is_search or not is_write else "hubspot_companies_write"
        elif "/crm/v3/objects/deals" in path:
            capability_key = "hubspot_deals_read" if is_search or not is_write else "hubspot_deals_write"
        elif "/crm/v3/owners" in path or "/crm/v3/properties" in path:
            capability_key = "hubspot_metadata_read"
    elif provider.key == SLACK_PROVIDER.key:
        if path.endswith("/conversations.list"):
            capability_key = "slack_channel_discovery"
        elif path.endswith("/chat.postmessage"):
            capability_key = "slack_messages_send"

    if not capability_key:
        return None
    try:
        return get_native_integration_capability(provider.key, capability_key)
    except KeyError:
        return None


def native_integration_setup_url() -> str:
    public_site_url = str(settings.PUBLIC_SITE_URL or "").strip().rstrip("/")
    if public_site_url:
        return f"{public_site_url}/app/integrations"
    return "/app/integrations"


def _parse_expires_at(value: object):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
    return parsed


def _should_refresh_oauth_credentials(credentials: dict[str, Any]) -> bool:
    if not credentials.get("access_token"):
        return True
    expires_at = _parse_expires_at(credentials.get("expires_at"))
    return bool(expires_at and expires_at <= timezone.now() + TOKEN_REFRESH_SKEW)


def refresh_oauth_credentials_if_needed(
    provider: NativeIntegrationProvider,
    secret: GlobalSecret,
    credentials: dict[str, Any],
) -> dict[str, Any]:
    if provider.auth_type != "oauth2" or not _should_refresh_oauth_credentials(credentials):
        return credentials

    refresh_token = str(credentials.get("refresh_token") or "")
    if not refresh_token:
        raise NativeIntegrationAuthError(
            f"{provider.display_name} must be reconnected.",
            **_native_integration_error_kwargs(
                provider,
                code="native_integration_reconnect_required",
                credentials=credentials,
                retryable=False,
            ),
        )

    client_id, client_secret = native_integration_client_credentials(provider)
    if not client_id or not client_secret:
        raise NativeIntegrationConfigurationError(
            f"{provider.display_name} OAuth is not configured.",
            **_native_integration_error_kwargs(
                provider,
                code="native_integration_oauth_not_configured",
                credentials=credentials,
                retryable=False,
            ),
        )

    token_payload = request_oauth_token(
        provider,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        request_error_message=f"{provider.display_name} token refresh failed.",
        endpoint_error_message=f"{provider.display_name} token refresh failed. Reconnect the app.",
        invalid_json_message=f"{provider.display_name} token refresh returned invalid data.",
    )

    updated = build_oauth_credentials_bundle(
        provider,
        token_payload,
        existing_credentials=credentials,
    )
    save_native_integration_credentials(provider, secret.user, secret.organization, updated)
    return updated


def list_google_drive_accessible_files(
    secret: GlobalSecret,
    *,
    page_size: int = 50,
) -> list[NativeIntegrationAccessibleFile]:
    provider = GOOGLE_DRIVE_PROVIDER
    credentials = load_native_integration_credentials(secret)
    credentials = refresh_oauth_credentials_if_needed(provider, secret, credentials)
    access_token = str(credentials.get("access_token") or "")
    if not access_token:
        raise NativeIntegrationAuthError(
            f"{provider.display_name} must be reconnected.",
            **_native_integration_error_kwargs(
                provider,
                code="native_integration_reconnect_required",
                credentials=credentials,
                retryable=False,
            ),
        )

    try:
        response = httpx.get(
            GOOGLE_DRIVE_FILES_URL,
            params={
                "pageSize": max(1, min(int(page_size), 100)),
                "fields": "files(id,name,mimeType,webViewLink)",
                "orderBy": "modifiedTime desc",
                "q": (
                    "trashed = false and "
                    f"(mimeType = '{GOOGLE_SHEETS_MIME_TYPE}' or mimeType = '{GOOGLE_DOCS_MIME_TYPE}')"
                ),
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise NativeIntegrationFileListError("Unable to load Google Drive files.", detail=str(exc)) from exc

    if response.status_code in {401, 403}:
        raise NativeIntegrationAuthError(f"{provider.display_name} must be reconnected.")
    if response.status_code >= 400:
        raise NativeIntegrationFileListError("Unable to load Google Drive files.", status_code=response.status_code)

    try:
        payload = response.json()
    except ValueError as exc:
        raise NativeIntegrationFileListError("Google Drive returned invalid file data.") from exc
    if not isinstance(payload, dict):
        raise NativeIntegrationFileListError("Google Drive returned invalid file data.")

    files = payload.get("files") or []
    if not isinstance(files, list):
        raise NativeIntegrationFileListError("Google Drive returned invalid file data.")

    results: list[NativeIntegrationAccessibleFile] = []
    for file_item in files:
        if not isinstance(file_item, dict):
            continue
        external_id = str(file_item.get("id") or "").strip()
        name = str(file_item.get("name") or "").strip()
        mime_type = str(file_item.get("mimeType") or "").strip()
        if not external_id or not name or mime_type not in {GOOGLE_SHEETS_MIME_TYPE, GOOGLE_DOCS_MIME_TYPE}:
            continue
        results.append(
            NativeIntegrationAccessibleFile(
                external_id=external_id,
                name=name,
                mime_type=mime_type,
                web_url=str(file_item.get("webViewLink") or "").strip(),
            )
        )
    return results


def apply_native_integration_auth(
    agent: PersistentAgent,
    url: str,
    headers: dict[str, str],
    *,
    method: str = "GET",
) -> dict[str, str]:
    provider = find_provider_for_url(url)
    if provider is None:
        return headers

    if any(key.lower() == "authorization" for key in headers):
        return headers

    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    if secret is None:
        raise NativeIntegrationAuthError(
            f"native_integration_not_connected: {provider.display_name} is not connected. "
            f"{_native_integration_not_connected_guidance(provider)}",
            **_native_integration_error_kwargs(
                provider,
                code="native_integration_not_connected",
                retryable=False,
            ),
        )

    credentials = load_native_integration_credentials(secret)
    credentials = refresh_oauth_credentials_if_needed(provider, secret, credentials)

    capability = native_integration_capability_for_request(provider, url, method=method)
    if capability is not None:
        granted_scopes = _credentials_granted_scopes(credentials, provider)
        missing_scopes = [
            scope
            for scope in capability.required_scopes
            if scope not in set(granted_scopes)
        ]
        if missing_scopes:
            raise NativeIntegrationAuthError(
                f"native_integration_missing_scopes: {provider.display_name} is connected, "
                f"but `{capability.label}` requires additional scopes: {', '.join(missing_scopes)}. "
                f"Ask the user to reconnect {provider.display_name} at {native_integration_setup_url()}.",
                **_native_integration_error_kwargs(
                    provider,
                    code="native_integration_missing_scopes",
                    credentials=credentials,
                    missing_scopes=missing_scopes,
                    retryable=False,
                ),
            )

    if provider.auth_type == "oauth2":
        access_token = str(credentials.get("access_token") or "")
        if not access_token:
            raise NativeIntegrationAuthError(
                f"{provider.display_name} must be reconnected.",
                **_native_integration_error_kwargs(
                    provider,
                    code="native_integration_reconnect_required",
                    credentials=credentials,
                    retryable=False,
                ),
            )
        token_type = str(credentials.get("token_type") or "Bearer").strip() or "Bearer"
        if token_type.lower() == "bearer":
            token_type = "Bearer"
        updated = dict(headers)
        updated["Authorization"] = f"{token_type} {access_token}"
        return updated

    return headers


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)
