"""Product-managed OAuth clients for owner-scoped remote MCP integrations."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone
from waffle import get_waffle_flag_model

from api.agent.system_skills.keys import HUBSPOT_NATIVE_SYSTEM_SKILL_KEY
from api.models import (
    MCPServerConfig,
    MCPServerOAuthCredential,
    MCPServerOAuthSession,
    PersistentAgentEnabledTool,
    PersistentAgentMCPServer,
    PersistentAgentSystemSkillState,
)


HUBSPOT_MCP_PROVIDER_KEY = "hubspot"
HUBSPOT_MCP_WAFFLE_FLAG = "hubspot_mcp"
MANAGED_MCP_OAUTH_SESSION_TTL = timedelta(minutes=10)
MANAGED_MCP_TOKEN_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class ManagedOAuthMCPProvider:
    key: str
    display_name: str
    description: str
    server_url: str
    authorization_endpoint: str
    token_endpoint: str
    token_endpoint_auth_method: str
    scopes: tuple[str, ...]
    icon: str
    waffle_flag: str
    system_skill_key: str
    client_credentials_resolver: Callable[[], tuple[str, str]]
    pipedream_app_slugs: tuple[str, ...] = ()


def _hubspot_mcp_client_credentials() -> tuple[str, str]:
    return settings.HUBSPOT_MCP_CLIENT_ID, settings.HUBSPOT_MCP_CLIENT_SECRET


HUBSPOT_MCP_PROVIDER = ManagedOAuthMCPProvider(
    key=HUBSPOT_MCP_PROVIDER_KEY,
    display_name="HubSpot",
    description=(
        "Connect HubSpot's remote MCP server for contacts, companies, deals, "
        "tickets, and other available CRM tools."
    ),
    server_url="https://mcp.hubspot.com/",
    authorization_endpoint="https://mcp.hubspot.com/oauth/authorize/user",
    token_endpoint="https://mcp.hubspot.com/oauth/v3/token",
    token_endpoint_auth_method="client_secret_post",
    scopes=(),
    icon="hubspot",
    waffle_flag=HUBSPOT_MCP_WAFFLE_FLAG,
    system_skill_key=HUBSPOT_NATIVE_SYSTEM_SKILL_KEY,
    client_credentials_resolver=_hubspot_mcp_client_credentials,
    pipedream_app_slugs=("hubspot",),
)


MANAGED_OAUTH_MCP_PROVIDERS = {
    HUBSPOT_MCP_PROVIDER.key: HUBSPOT_MCP_PROVIDER,
}


class ManagedMCPConfigurationError(ValueError):
    pass


class ManagedMCPTokenRequestError(ValueError):
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


class ManagedMCPConnectionMode(str, Enum):
    LEGACY_REST = "legacy_rest"
    MANAGED_MCP = "managed_mcp"


def get_managed_mcp_provider(provider_key: str) -> ManagedOAuthMCPProvider:
    normalized_key = str(provider_key or "").strip().lower()
    provider = MANAGED_OAUTH_MCP_PROVIDERS.get(normalized_key)
    if provider is None:
        raise KeyError(provider_key)
    return provider


def managed_mcp_client_credentials(provider: ManagedOAuthMCPProvider) -> tuple[str, str]:
    return provider.client_credentials_resolver()


def managed_mcp_client_is_configured(provider: ManagedOAuthMCPProvider) -> bool:
    client_id, client_secret = managed_mcp_client_credentials(provider)
    if not client_id:
        return False
    return provider.token_endpoint_auth_method == "none" or bool(client_secret)


def build_managed_mcp_token_request(
    provider: ManagedOAuthMCPProvider,
    data: dict[str, str],
) -> tuple[dict[str, str], tuple[str, str] | None]:
    client_id, client_secret = managed_mcp_client_credentials(provider)
    if not managed_mcp_client_is_configured(provider):
        raise ManagedMCPConfigurationError(f"{provider.display_name} MCP OAuth is not configured.")

    request_data = dict(data)
    if provider.token_endpoint_auth_method == "client_secret_post":
        request_data.update({"client_id": client_id, "client_secret": client_secret})
        return request_data, None
    if provider.token_endpoint_auth_method == "client_secret_basic":
        return request_data, (client_id, client_secret)
    if provider.token_endpoint_auth_method == "none":
        request_data["client_id"] = client_id
        return request_data, None
    raise ManagedMCPConfigurationError(
        f"Unsupported managed MCP token authentication method: {provider.token_endpoint_auth_method}."
    )


def _rollout_user(owner_user, owner_org):
    if owner_org is not None:
        return owner_org.created_by
    return owner_user


def _stable_percentage_rollout_enabled(flag_name: str, owner_user, owner_org, percent) -> bool:
    if owner_org is not None:
        owner_key = f"organization:{owner_org.pk}"
    elif owner_user is not None:
        owner_key = f"user:{owner_user.pk}"
    else:
        return False

    # Waffle percentage assignments normally persist in a browser cookie. Background
    # agents have no such cookie, so use a stable owner cohort shared by every worker.
    digest = hashlib.sha256(f"{flag_name}:{owner_key}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % 1000
    threshold = max(0, min(int(Decimal(percent) * Decimal("10")), 1000))
    return bucket < threshold


def managed_mcp_provider_enabled(provider_key: str, owner_user, owner_org) -> bool:
    """Evaluate rollout against the persistent owner, not the current request actor."""

    try:
        provider = get_managed_mcp_provider(provider_key)
    except KeyError:
        return False
    user = _rollout_user(owner_user, owner_org)
    if user is None:
        return False

    flag = get_waffle_flag_model().get(provider.waffle_flag)
    request = HttpRequest()
    request.user = user
    if not flag.pk:
        return bool(flag.is_active(request))
    if flag.everyone is not None:
        return bool(flag.everyone)

    active_for_user = flag.is_active_for_user(user)
    if active_for_user is not None:
        return bool(active_for_user)
    if flag.percent and flag.percent > 0:
        return _stable_percentage_rollout_enabled(
            provider.waffle_flag,
            owner_user,
            owner_org,
            flag.percent,
        )
    return False


def managed_mcp_provider_keys_for_agent(agent) -> set[str]:
    owner_user = None if agent.organization_id else agent.user
    owner_org = agent.organization if agent.organization_id else None
    return {
        provider.key
        for provider in MANAGED_OAUTH_MCP_PROVIDERS.values()
        if resolve_managed_mcp_connection_mode(provider.key, owner_user, owner_org)
        == ManagedMCPConnectionMode.MANAGED_MCP
    }


def managed_mcp_suppressed_pipedream_app_slugs(owner_user, owner_org) -> set[str]:
    """Return Pipedream apps replaced by a native integration for this owner."""

    from api.services.native_integrations import get_native_integration_secret

    suppressed: set[str] = set()
    for provider in MANAGED_OAUTH_MCP_PROVIDERS.values():
        rollout_enabled = managed_mcp_provider_enabled(provider.key, owner_user, owner_org)
        has_legacy_connection = get_native_integration_secret(
            provider.key,
            owner_user,
            owner_org,
        ) is not None
        if rollout_enabled or has_legacy_connection:
            suppressed.update(provider.pipedream_app_slugs)
    return suppressed


def managed_mcp_config_queryset(provider_key: str, owner_user, owner_org):
    queryset = MCPServerConfig.objects.filter(managed_integration_key=provider_key)
    if owner_org is not None:
        return queryset.filter(
            scope=MCPServerConfig.Scope.ORGANIZATION,
            organization=owner_org,
        )
    return queryset.filter(
        scope=MCPServerConfig.Scope.USER,
        user=owner_user,
    )


def get_managed_mcp_config(
    provider_key: str,
    owner_user,
    owner_org,
    *,
    active_only: bool = False,
) -> MCPServerConfig | None:
    queryset = managed_mcp_config_queryset(provider_key, owner_user, owner_org)
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.select_related("oauth_credential").first()


def resolve_managed_mcp_connection_mode(
    provider_key: str,
    owner_user,
    owner_org,
) -> ManagedMCPConnectionMode:
    """Choose one HubSpot transport for the workspace before tools are exposed."""

    if not managed_mcp_provider_enabled(provider_key, owner_user, owner_org):
        return ManagedMCPConnectionMode.LEGACY_REST

    config = get_managed_mcp_config(provider_key, owner_user, owner_org)
    if config is not None:
        has_managed_credential = MCPServerOAuthCredential.objects.filter(
            server_config=config,
        ).exists()
        if config.is_active or has_managed_credential:
            return ManagedMCPConnectionMode.MANAGED_MCP

    # Keep installed REST users grandfathered until MCP authorization succeeds.
    # The local import avoids making native_integrations and this module import each
    # other while they are being initialized.
    from api.services.native_integrations import get_native_integration_secret

    if get_native_integration_secret(provider_key, owner_user, owner_org) is not None:
        return ManagedMCPConnectionMode.LEGACY_REST
    return ManagedMCPConnectionMode.MANAGED_MCP


def managed_mcp_connection_summary(provider_key: str, owner_user, owner_org) -> dict[str, Any]:
    provider = get_managed_mcp_provider(provider_key)
    config = get_managed_mcp_config(provider.key, owner_user, owner_org, active_only=True)
    credential = None
    if config is not None:
        try:
            credential = config.oauth_credential
        except MCPServerOAuthCredential.DoesNotExist:
            credential = None
    return {
        "provider": provider,
        "config": config,
        "credential": credential,
        "connected": config is not None and credential is not None and bool(credential.access_token),
        "scope": credential.scope if credential is not None else "",
        "expires_at": credential.expires_at if credential is not None else None,
    }


def managed_mcp_is_connected(provider_key: str, owner_user, owner_org) -> bool:
    if (
        resolve_managed_mcp_connection_mode(provider_key, owner_user, owner_org)
        != ManagedMCPConnectionMode.MANAGED_MCP
    ):
        return False
    return bool(managed_mcp_connection_summary(provider_key, owner_user, owner_org)["connected"])


def _owner_scope_and_values(owner_user, owner_org) -> tuple[str, dict[str, Any]]:
    if owner_org is not None:
        return MCPServerConfig.Scope.ORGANIZATION, {
            "organization": owner_org,
            "user": None,
        }
    if owner_user is None:
        raise ValidationError({"owner": "A managed MCP integration requires a workspace owner."})
    return MCPServerConfig.Scope.USER, {
        "organization": None,
        "user": owner_user,
    }


def upsert_managed_mcp_config(
    provider_key: str,
    owner_user,
    owner_org,
) -> MCPServerConfig:
    provider = get_managed_mcp_provider(provider_key)
    scope, owner_values = _owner_scope_and_values(owner_user, owner_org)
    owner = owner_org or owner_user
    with transaction.atomic():
        owner._meta.model._default_manager.select_for_update().get(pk=owner.pk)
        existing_name = MCPServerConfig.objects.filter(
            scope=scope,
            name=provider.key,
            **owner_values,
        ).exclude(managed_integration_key=provider.key).exists()
        if existing_name:
            raise ValidationError(
                {
                    "provider": (
                        f"Rename the existing `{provider.key}` MCP server before connecting "
                        f"the managed {provider.display_name} integration."
                    )
                }
            )

        existing_config = MCPServerConfig.objects.filter(
            scope=scope,
            managed_integration_key=provider.key,
            **owner_values,
        ).first()

        defaults = {
            "name": provider.key,
            "display_name": provider.display_name,
            "description": provider.description,
            "command": "",
            "command_args": [],
            "url": provider.server_url,
            "auth_method": MCPServerConfig.AuthMethod.OAUTH2,
            "metadata": {
                "managed_oauth": True,
                "provider_key": provider.key,
            },
            "is_active": existing_config.is_active if existing_config is not None else False,
            **owner_values,
        }
        try:
            config, _created = MCPServerConfig.objects.update_or_create(
                scope=scope,
                managed_integration_key=provider.key,
                **owner_values,
                defaults=defaults,
            )
        except IntegrityError as exc:
            raise ValidationError(
                {"provider": f"Unable to reserve the {provider.display_name} MCP server identifier."}
            ) from exc
        config.full_clean()
        return config


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def start_managed_mcp_oauth(
    provider_key: str,
    *,
    initiated_by,
    owner_user,
    owner_org,
    redirect_uri: str,
) -> dict[str, Any]:
    provider = get_managed_mcp_provider(provider_key)
    if not managed_mcp_provider_enabled(provider.key, owner_user, owner_org):
        raise ManagedMCPConfigurationError(f"{provider.display_name} MCP is not enabled for this workspace.")
    client_id, _client_secret = managed_mcp_client_credentials(provider)
    if not managed_mcp_client_is_configured(provider):
        raise ManagedMCPConfigurationError(f"{provider.display_name} MCP OAuth is not configured.")

    config = upsert_managed_mcp_config(provider.key, owner_user, owner_org)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    expires_at = timezone.now() + MANAGED_MCP_OAUTH_SESSION_TTL
    requested_scope = " ".join(provider.scopes)
    session = MCPServerOAuthSession(
        server_config=config,
        initiated_by=initiated_by,
        organization=owner_org,
        user=owner_user if owner_org is None else None,
        state=state,
        redirect_uri=redirect_uri,
        scope=requested_scope,
        code_challenge=challenge,
        code_challenge_method="S256",
        token_endpoint=provider.token_endpoint,
        client_id="",
        metadata={"managed_integration_key": provider.key},
        expires_at=expires_at,
    )
    session.code_verifier = verifier
    session.full_clean()
    session.save()

    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if requested_scope:
        query["scope"] = requested_scope
    return {
        "provider_key": provider.key,
        "authorization_url": f"{provider.authorization_endpoint}?{urlencode(query)}",
        "state": state,
        "expires_at": expires_at,
        "session": session,
        "config": config,
    }


def managed_mcp_oauth_session_exists(provider_key: str, state: str, initiated_by) -> bool:
    return MCPServerOAuthSession.objects.filter(
        state=state,
        initiated_by=initiated_by,
        server_config__managed_integration_key=provider_key,
    ).exists()


def _consume_managed_session(
    provider: ManagedOAuthMCPProvider,
    *,
    state: str,
    initiated_by,
    owner_user,
    owner_org,
) -> MCPServerOAuthSession:
    expired = False
    with transaction.atomic():
        try:
            session = MCPServerOAuthSession.objects.select_for_update().select_related("server_config").get(
                state=state,
                initiated_by=initiated_by,
                server_config__managed_integration_key=provider.key,
            )
        except MCPServerOAuthSession.DoesNotExist as exc:
            raise ValidationError({"state": "OAuth session expired. Restart the flow."}) from exc
        if session.has_expired():
            MCPServerOAuthSession.objects.filter(pk=session.pk).delete()
            expired = True
        else:
            if owner_org is not None:
                owner_matches = session.organization_id == owner_org.id and session.user_id is None
            else:
                owner_matches = session.user_id == owner_user.id and session.organization_id is None
            if not owner_matches:
                raise ValidationError({"state": "OAuth session belongs to another workspace context."})
            if not managed_mcp_provider_enabled(provider.key, owner_user, owner_org):
                raise ValidationError(
                    {"provider": f"{provider.display_name} MCP is no longer enabled for this workspace."}
                )
            MCPServerOAuthSession.objects.filter(pk=session.pk).delete()
    if expired:
        raise ValidationError({"state": "OAuth session expired. Restart the flow."})
    return session


def _request_token(provider: ManagedOAuthMCPProvider, data: dict[str, str]) -> dict[str, Any]:
    request_data, request_auth = build_managed_mcp_token_request(provider, data)
    request_kwargs: dict[str, Any] = {
        "data": request_data,
        "timeout": MANAGED_MCP_TOKEN_TIMEOUT_SECONDS,
    }
    if request_auth is not None:
        request_kwargs["auth"] = request_auth
    try:
        response = httpx.post(provider.token_endpoint, **request_kwargs)
    except httpx.HTTPError as exc:
        raise ManagedMCPTokenRequestError("Token exchange failed.", detail=str(exc)) from exc
    if response.status_code >= 400:
        raise ManagedMCPTokenRequestError(
            "Token endpoint returned an error.",
            status_code=response.status_code,
            response_body=response.text,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ManagedMCPTokenRequestError("Token endpoint returned invalid data.") from exc
    if not isinstance(payload, dict):
        raise ManagedMCPTokenRequestError("Token endpoint returned invalid data.")
    if not str(payload.get("access_token") or "").strip():
        raise ManagedMCPTokenRequestError("Token response missing access_token.")
    return payload


def complete_managed_mcp_oauth(
    provider_key: str,
    *,
    state: str,
    authorization_code: str,
    initiated_by,
    owner_user,
    owner_org,
) -> dict[str, Any]:
    provider = get_managed_mcp_provider(provider_key)
    if not managed_mcp_client_is_configured(provider):
        raise ManagedMCPConfigurationError(f"{provider.display_name} MCP OAuth is not configured.")
    session = _consume_managed_session(
        provider,
        state=state,
        initiated_by=initiated_by,
        owner_user=owner_user,
        owner_org=owner_org,
    )
    token_payload = _request_token(
        provider,
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": session.redirect_uri,
            "code_verifier": session.code_verifier,
        },
    )
    config = session.server_config
    try:
        credential = config.oauth_credential
    except MCPServerOAuthCredential.DoesNotExist:
        credential = MCPServerOAuthCredential(server_config=config)

    prior_refresh_token = credential.refresh_token
    credential.organization = owner_org
    credential.user = owner_user if owner_org is None else None
    credential.client_id = ""
    credential.client_secret_encrypted = None
    credential.access_token = str(token_payload["access_token"]).strip()
    credential.refresh_token = str(token_payload.get("refresh_token") or prior_refresh_token or "")
    credential.id_token = str(token_payload.get("id_token") or "")
    credential.token_type = str(token_payload.get("token_type") or credential.token_type or "Bearer")
    credential.scope = str(token_payload.get("scope") or session.scope)
    try:
        expires_in = token_payload.get("expires_in")
        credential.expires_at = (
            None
            if expires_in is None
            else timezone.now() + timedelta(seconds=max(int(expires_in), 0))
        )
    except (TypeError, ValueError):
        credential.expires_at = None
    credential.metadata = {
        "managed_integration_key": provider.key,
        "last_token_response": {
            key: value
            for key, value in token_payload.items()
            if key in {"expires_in", "scope", "token_type"}
        },
    }

    with transaction.atomic():
        credential.save()
        if not config.is_active:
            config.is_active = True
            config.save(update_fields=["is_active", "updated_at"])

    from api.agent.tools.mcp_manager import get_mcp_manager

    get_mcp_manager().refresh_server(str(config.id))
    return {
        "provider": provider,
        "config": config,
        "credential": credential,
    }


def disconnect_managed_mcp(provider_key: str, owner_user, owner_org) -> bool:
    provider = get_managed_mcp_provider(provider_key)
    config = get_managed_mcp_config(provider.key, owner_user, owner_org)
    if config is None:
        return False
    had_connection = config.is_active or MCPServerOAuthCredential.objects.filter(server_config=config).exists()
    with transaction.atomic():
        MCPServerOAuthSession.objects.filter(server_config=config).delete()
        MCPServerOAuthCredential.objects.filter(server_config=config).delete()
        PersistentAgentEnabledTool.objects.filter(server_config=config).delete()
        PersistentAgentMCPServer.objects.filter(server_config=config).delete()
        if config.is_active:
            config.is_active = False
            config.save(update_fields=["is_active", "updated_at"])

    from api.agent.tools.mcp_manager import get_mcp_manager

    get_mcp_manager().remove_server(str(config.id))
    return had_connection


def trigger_agents_for_managed_mcp_change(provider_key: str, owner_user, owner_org) -> int:
    provider = get_managed_mcp_provider(provider_key)
    states = PersistentAgentSystemSkillState.objects.filter(
        skill_key=provider.system_skill_key,
        is_enabled=True,
        agent__is_deleted=False,
        agent__is_active=True,
    )
    if owner_org is not None:
        states = states.filter(agent__organization=owner_org)
    else:
        states = states.filter(agent__user=owner_user, agent__organization__isnull=True)
    agent_ids = list(states.values_list("agent_id", flat=True).distinct())
    if not agent_ids:
        return 0

    from api.agent.tasks.process_events import process_agent_events_task

    def _enqueue() -> None:
        for agent_id in agent_ids:
            process_agent_events_task.delay(str(agent_id))

    transaction.on_commit(_enqueue)
    return len(agent_ids)
