"""
Google Workspace OAuth helpers for Docs/Sheets tooling.

Provides scope tier resolution, binding lookup, and credential refresh scaffolding.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

from django.conf import settings
from django.utils import timezone
from django.core.cache import cache
from django.core.mail import send_mail

from api.models import AgentGoogleWorkspaceBinding, GoogleWorkspaceCredential, PersistentAgent

logger = logging.getLogger(__name__)

# Canonical scope tiers (Iteration 1: minimal + search-enabled; full reserved for later)
GOOGLE_SCOPE_TIERS: dict[str, list[str]] = {
    "minimal": [
        # "https://www.googleapis.com/auth/documents",
        # "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.appdata",
        "https://www.googleapis.com/auth/drive.appfolder",
        "https://www.googleapis.com/auth/drive.file",
        "openid",
        "email",
        "profile",
    ],
    "search_enabled": [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
        "openid",
        "email",
        "profile",
    ],
    "full": [
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
        "openid",
        "email",
        "profile",
    ],
}

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _unique_scopes(scopes: Iterable[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for scope in scopes or []:
        if not scope:
            continue
        if scope in seen:
            continue
        seen.add(scope)
        ordered.append(scope)
    return ordered


def scope_list_for_tier(scope_tier: str | None) -> list[str]:
    tier = (scope_tier or settings.GOOGLE_WORKSPACE_DEFAULT_SCOPE_TIER or "minimal").strip().lower()
    return _unique_scopes(GOOGLE_SCOPE_TIERS.get(tier, GOOGLE_SCOPE_TIERS["minimal"]))


# Google returns full URIs for some scopes we request as shorthand
SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}


def _normalize_scope(scope: str) -> str:
    """Normalize a scope to its canonical form for comparison."""
    scope = scope.strip()
    # Check if this is a shorthand that maps to a full URI
    if scope in SCOPE_ALIASES:
        return SCOPE_ALIASES[scope]
    # Check if this is a full URI that maps from a shorthand
    for shorthand, full_uri in SCOPE_ALIASES.items():
        if scope == full_uri:
            return full_uri
    return scope


def scopes_satisfy(required: Sequence[str], granted: Sequence[str]) -> bool:
    if not required:
        return True
    granted_normalized = {_normalize_scope(s) for s in granted or [] if s}
    return all(_normalize_scope(scope) in granted_normalized for scope in required)


def build_connect_url(agent_id: str, scope_tier: str | None = None) -> str:
    base = getattr(settings, "GOOGLE_WORKSPACE_CONNECT_URL", "") or ""
    if not base:
        try:
            from django.urls import reverse

            base = reverse("google_workspace_connect", kwargs={"agent_id": agent_id})
        except Exception:
            base = f"/console/google/connect/{agent_id}/"
    else:
        # Append agent_id to configured base URL
        base = base.rstrip("/") + f"/{agent_id}/"
    if scope_tier:
        separator = "&" if "?" in base else "?"
        base = f"{base}{separator}scope_tier={scope_tier}"
    # Prefer absolute URL for email delivery
    public_site = getattr(settings, "PUBLIC_SITE_URL", "").rstrip("/")
    if public_site and not base.startswith("http"):
        return f"{public_site}{base}"
    return base


@dataclass
class BoundGoogleCredential:
    binding: AgentGoogleWorkspaceBinding
    credential: GoogleWorkspaceCredential
    scopes: list[str]
    scope_tier: str


def resolve_binding(
    agent: PersistentAgent,
    *,
    required_scope_tier: Optional[str] = None,
) -> tuple[Optional[BoundGoogleCredential], Optional[dict]]:
    """Resolve the agent's Google binding or return an action_required response."""

    if not getattr(settings, "GOOGLE_WORKSPACE_TOOLS_ENABLED", False):
        return None, {
            "status": "action_required",
            "result": (
                "Google Docs/Sheets tools are disabled in this environment. "
                "Please enable GOOGLE_WORKSPACE_TOOLS_ENABLED and configure OAuth."
            ),
        }


    try:
        binding = agent.google_workspace_binding  # type: ignore[attr-defined]
    except AgentGoogleWorkspaceBinding.DoesNotExist:
        binding = None
    except Exception:
        logger.exception("Failed to load Google Workspace binding for agent %s", getattr(agent, "id", None))
        binding = None

    if not binding or not binding.credential:
        url = build_connect_url(str(agent.id), required_scope_tier)
        _maybe_email_connect_link(agent, url, required_scope_tier)
        return None, {
            "status": "action_required",
            "result": (
                "Google Docs/Sheets access is not connected for this agent. "
                f"Please connect your Google account here: {url}"
            ),
            "connect_url": url,
        }

    credential = binding.credential
    granted_scopes = credential.scopes_list()
    target_scopes = scope_list_for_tier(required_scope_tier or binding.scope_tier or credential.scope_tier)
    if not scopes_satisfy(target_scopes, granted_scopes):
        url = build_connect_url(
            str(agent.id),
            required_scope_tier or binding.scope_tier or credential.scope_tier,
        )
        _maybe_email_connect_link(agent, url, required_scope_tier or binding.scope_tier or credential.scope_tier)
        return None, {
            "status": "action_required",
            "result": (
                "Additional Google permissions are required to continue. "
                f"Please re-connect with the broader scope here: {url}"
            ),
            "connect_url": url,
        }

    scope_tier = required_scope_tier or binding.scope_tier or credential.scope_tier or settings.GOOGLE_WORKSPACE_DEFAULT_SCOPE_TIER
    bundle = BoundGoogleCredential(
        binding=binding,
        credential=credential,
        scopes=granted_scopes,
        scope_tier=scope_tier,
    )
    return bundle, None


def _maybe_email_connect_link(agent: PersistentAgent, url: str, scope_tier: Optional[str]) -> None:
    """Email the agent owner a connect link, throttled to avoid spam."""
    try:
        owner_email = getattr(agent.user, "email", "") or ""
        if not owner_email:
            return
        cache_key = f"gws_connect_email_sent:{agent.id}"
        if cache.get(cache_key):
            return

        subject = "Connect Google Docs/Sheets for your agent"
        scope_label = scope_tier or getattr(settings, "GOOGLE_WORKSPACE_DEFAULT_SCOPE_TIER", "minimal")
        message = (
            f"Hi,\n\n"
            f"To enable Google Docs/Sheets actions for your agent '{getattr(agent, 'name', '')}', "
            f"please connect your Google account here:\n{url}\n\n"
            f"Scope tier requested: {scope_label}\n\n"
            f"If you did not request this, you can ignore this email.\n"
        )
        send_mail(subject, message, getattr(settings, "DEFAULT_FROM_EMAIL", None), [owner_email], fail_silently=True)
        cache.set(cache_key, True, timeout=3600)  # 1 hour throttle
    except Exception:
        logger.exception("Failed to send Google Workspace connect email for agent %s", getattr(agent, "id", None))


def _build_google_credentials(bundle: BoundGoogleCredential):
    """Create a google.oauth2.credentials.Credentials object with refresh support."""
    try:
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None, {
            "status": "error",
            "message": (
                "Google auth libraries are not installed. "
                "Please install google-auth and related dependencies."
            ),
        }

    scopes = bundle.scopes or scope_list_for_tier(bundle.scope_tier)
    expiry = bundle.credential.expires_at
    # google-auth internally uses naive UTC datetimes, so convert if aware
    if expiry is not None and expiry.tzinfo is not None:
        from datetime import timezone as dt_timezone
        expiry = expiry.astimezone(dt_timezone.utc).replace(tzinfo=None)
    credentials = Credentials(
        token=bundle.credential.access_token or None,
        refresh_token=bundle.credential.refresh_token or None,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=getattr(settings, "GOOGLE_WORKSPACE_CLIENT_ID", "") or None,
        client_secret=getattr(settings, "GOOGLE_WORKSPACE_CLIENT_SECRET", "") or None,
        scopes=scopes,
        expiry=expiry,
    )
    return credentials, None


def ensure_fresh_credentials(bundle: BoundGoogleCredential):
    """Ensure the credential is valid; refresh if expired."""
    credentials, err = _build_google_credentials(bundle)
    if err:
        return None, err

    # Credentials without refresh token cannot be refreshed; surface reconnect.
    if not credentials.refresh_token and not credentials.valid:
        url = build_connect_url(bundle.scope_tier)
        return None, {
            "status": "action_required",
            "result": "Google authorization is missing or expired. Please reconnect.",
            "connect_url": url,
        }

    if credentials.expired or not credentials.valid:
        try:
            from google.auth.transport.requests import Request
        except ImportError:
            return None, {
                "status": "error",
                "message": (
                    "google-auth-transport not installed; cannot refresh credentials. "
                    "Install google-auth-httplib2 or google-auth transport dependencies."
                ),
            }

        try:
            credentials.refresh(Request())
        except Exception as exc:  # pragma: no cover - network/API dependent
            logger.warning(
                "Failed refreshing Google credentials for agent %s: %s",
                getattr(bundle.binding.agent, "id", None),
                exc,
            )
            url = build_connect_url(bundle.scope_tier)
            return None, {
                "status": "action_required",
                "result": "Google authorization expired or invalid. Please reconnect.",
                "connect_url": url,
            }

        # Persist refreshed tokens
        bundle.credential.access_token = credentials.token
        try:
            # google-auth returns UTC-aware expiry
            bundle.credential.expires_at = timezone.make_aware(credentials.expiry) if credentials.expiry else None
        except Exception:
            bundle.credential.expires_at = credentials.expiry
        bundle.credential.token_type = credentials.token_response.get("token_type", "") if hasattr(credentials, "token_response") else bundle.credential.token_type
        bundle.credential.save(update_fields=["access_token_encrypted", "expires_at", "token_type", "updated_at"])

    return credentials, None
