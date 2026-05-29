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


class NativeIntegrationError(Exception):
    """Base error for native integration failures."""


class NativeIntegrationConfigurationError(NativeIntegrationError):
    """Raised when a provider is not configured on this deployment."""


class NativeIntegrationAuthError(NativeIntegrationError):
    """Raised when a stored integration cannot authenticate a request."""


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
    icon: str
    authorization_params: dict[str, str]

    @property
    def secret_key(self) -> str:
        return f"{NATIVE_INTEGRATION_SECRET_PREFIX}{self.key}"

    @property
    def scope_string(self) -> str:
        return " ".join(self.scopes)


GOOGLE_SHEETS_PROVIDER = NativeIntegrationProvider(
    key="google_sheets",
    display_name="Google Sheets",
    description="Read and edit spreadsheets through the Google Sheets API.",
    auth_type="oauth2",
    authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    scopes=("https://www.googleapis.com/auth/drive.file",),
    api_hosts=("sheets.googleapis.com",),
    icon="google_sheets",
    authorization_params={
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    },
)

NATIVE_INTEGRATION_PROVIDERS = {
    GOOGLE_SHEETS_PROVIDER.key: GOOGLE_SHEETS_PROVIDER,
}


def list_native_integration_providers() -> list[NativeIntegrationProvider]:
    return list(NATIVE_INTEGRATION_PROVIDERS.values())


def get_native_integration_provider(provider_key: str) -> NativeIntegrationProvider:
    provider = NATIVE_INTEGRATION_PROVIDERS.get(str(provider_key or "").strip())
    if provider is None:
        raise KeyError(provider_key)
    return provider


def native_integration_client_credentials(provider: NativeIntegrationProvider) -> tuple[str, str]:
    if provider.key == GOOGLE_SHEETS_PROVIDER.key:
        return settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET
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


def get_native_integration_secret(provider_key: str, owner_user, owner_org) -> GlobalSecret | None:
    provider = get_native_integration_provider(provider_key)
    return native_integration_secret_queryset(owner_user, owner_org).filter(key=provider.secret_key).first()


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

    secret.set_value(json.dumps(credentials, separators=(",", ":"), sort_keys=True))
    secret.save()
    return secret


def delete_native_integration_credentials(provider_key: str, owner_user, owner_org) -> bool:
    secret = get_native_integration_secret(provider_key, owner_user, owner_org)
    if secret is None:
        return False
    secret.delete()
    return True


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
            "scopes": list(provider.scopes),
            "last_token_response": {
                key: value
                for key, value in token_payload.items()
                if key not in {"access_token", "refresh_token", "id_token"}
            },
        },
    }


def provider_matches_url(provider: NativeIntegrationProvider, url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
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

    try:
        response = httpx.post(
            provider.token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise NativeIntegrationAuthError(f"{provider.display_name} token refresh failed.") from exc

    if response.status_code >= 400:
        raise NativeIntegrationAuthError(f"{provider.display_name} token refresh failed. Reconnect the app.")

    try:
        token_payload = response.json()
    except ValueError as exc:
        raise NativeIntegrationAuthError(f"{provider.display_name} token refresh returned invalid data.") from exc

    updated = build_oauth_credentials_bundle(
        provider,
        token_payload,
        existing_credentials=credentials,
    )
    save_native_integration_credentials(provider, secret.user, secret.organization, updated)
    return updated


def apply_native_integration_auth(agent: PersistentAgent, url: str, headers: dict[str, str]) -> dict[str, str]:
    provider = find_provider_for_url(url)
    if provider is None:
        return headers

    if any(key.lower() == "authorization" for key in headers):
        return headers

    owner_user, owner_org = resolve_global_secret_owner_for_agent(agent)
    secret = get_native_integration_secret(provider.key, owner_user, owner_org)
    if secret is None:
        return headers

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
