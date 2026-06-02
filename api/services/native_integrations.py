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

from api.models import GlobalSecret, PersistentAgent
from api.services.persistent_agent_secrets import resolve_global_secret_owner_for_agent

logger = logging.getLogger(__name__)

NATIVE_INTEGRATION_SECRET_PREFIX = "native_"
TOKEN_REFRESH_SKEW = timedelta(minutes=5)
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"


class NativeIntegrationError(Exception):
    """Base error for native integration failures."""


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
        "include_granted_scopes": "true",
        "prompt": "consent",
    },
)

GOOGLE_SHEETS_PROVIDER = GOOGLE_DRIVE_PROVIDER
GOOGLE_DRIVE_PROVIDER_ALIASES = ("google_sheets",)
GOOGLE_DRIVE_LEGACY_SECRET_KEYS = ("native_google_sheets",)

NATIVE_INTEGRATION_PROVIDERS = {
    GOOGLE_DRIVE_PROVIDER.key: GOOGLE_DRIVE_PROVIDER,
}


def list_native_integration_providers() -> list[NativeIntegrationProvider]:
    return list(NATIVE_INTEGRATION_PROVIDERS.values())


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


def _native_integration_secret_keys(provider: NativeIntegrationProvider) -> list[str]:
    keys = [provider.secret_key]
    if provider.key == GOOGLE_DRIVE_PROVIDER.key:
        keys.extend(GOOGLE_DRIVE_LEGACY_SECRET_KEYS)
    return keys


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

    return {
        "provider_key": provider.key,
        "auth_type": provider.auth_type,
        "access_token": access_token,
        "refresh_token": str(refresh_token or ""),
        "token_type": str(token_payload.get("token_type") or "Bearer"),
        "scope": str(token_payload.get("scope") or provider.scope_string),
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
        raise NativeIntegrationAuthError(f"{provider.display_name} must be reconnected.")

    client_id, client_secret = native_integration_client_credentials(provider)
    if not client_id or not client_secret:
        raise NativeIntegrationConfigurationError(f"{provider.display_name} OAuth is not configured.")

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
        raise NativeIntegrationAuthError(f"{provider.display_name} must be reconnected.")

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


def apply_native_integration_auth(agent: PersistentAgent, url: str, headers: dict[str, str]) -> dict[str, str]:
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
            f"Ask the user to open {native_integration_setup_url()}, connect Google Drive, "
            "and choose the relevant file."
        )

    credentials = load_native_integration_credentials(secret)
    credentials = refresh_oauth_credentials_if_needed(provider, secret, credentials)

    if provider.auth_type == "oauth2":
        access_token = str(credentials.get("access_token") or "")
        if not access_token:
            raise NativeIntegrationAuthError(f"{provider.display_name} must be reconnected.")
        token_type = str(credentials.get("token_type") or "Bearer")
        updated = dict(headers)
        updated["Authorization"] = f"{token_type} {access_token}"
        return updated

    return headers


def new_oauth_state() -> str:
    return secrets.token_urlsafe(32)
