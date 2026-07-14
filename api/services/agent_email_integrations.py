import logging
from datetime import timedelta
from typing import Any

import httpx
import jwt
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from api.models import (
    AgentEmailAccount,
    AgentEmailIntegration,
    AgentEmailOAuthCredential,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
)
from api.services.agent_email_aliases import get_default_agent_email_endpoint


logger = logging.getLogger(__name__)

EMAIL_NATIVE_PROVIDER_KEYS = ("gmail", "outlook")
OUTLOOK_CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"
OUTLOOK_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"


def canonical_email_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in {"google", "gmail"}:
        return "gmail"
    if normalized in {"microsoft", "outlook", "o365", "office365"}:
        return "outlook"
    return normalized


def is_native_email_provider(provider: str) -> bool:
    return canonical_email_provider(provider) in EMAIL_NATIVE_PROVIDER_KEYS


def get_or_create_agent_email_integration(agent: PersistentAgent) -> AgentEmailIntegration:
    integration, _ = AgentEmailIntegration.objects.get_or_create(agent=agent)
    return integration


def oauth_credential_for_account(account: AgentEmailAccount | None) -> AgentEmailOAuthCredential | None:
    if account is None:
        return None
    try:
        return account.oauth_credential
    except AgentEmailOAuthCredential.DoesNotExist:
        return None


def active_external_email_account(agent: PersistentAgent) -> AgentEmailAccount | None:
    integration = AgentEmailIntegration.objects.filter(agent=agent).select_related(
        "custom_account__endpoint", "oauth_account__endpoint"
    ).first()
    if integration is None:
        return None
    return integration.active_account()


def active_native_email_provider(agent: PersistentAgent) -> str:
    account = active_external_email_account(agent)
    credential = oauth_credential_for_account(account)
    if credential is None:
        return ""
    provider = canonical_email_provider(credential.provider)
    return provider if provider in EMAIL_NATIVE_PROVIDER_KEYS else ""


def _validated_mailbox_address(value: object) -> str:
    address = str(value or "").strip().lower()
    try:
        validate_email(address)
    except ValidationError as exc:
        raise ValidationError({"mailbox": "The provider did not return a valid mailbox address."}) from exc
    return address


def _gmail_identity(access_token: str) -> dict[str, str]:
    try:
        response = httpx.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )
        response.raise_for_status()
        claims = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError({"mailbox": "Google mailbox identity could not be verified."}) from exc
    if not isinstance(claims, dict) or claims.get("email_verified") is not True:
        raise ValidationError({"mailbox": "Google did not return a verified mailbox address."})
    return {
        "address": _validated_mailbox_address(claims.get("email")),
        "display_name": str(claims.get("name") or "").strip(),
        "account_type": "gmail",
    }


def _outlook_identity(id_token: str, client_id: str) -> dict[str, str]:
    if not id_token:
        raise ValidationError({"mailbox": "Microsoft did not return identity information."})
    try:
        signing_key = jwt.PyJWKClient(OUTLOOK_JWKS_URL).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            options={"verify_iss": False},
        )
    except jwt.PyJWTError as exc:
        raise ValidationError({"mailbox": "Microsoft mailbox identity could not be verified."}) from exc

    tenant_id = str(claims.get("tid") or "").strip().lower()
    issuer = str(claims.get("iss") or "").strip().lower()
    expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
    if not tenant_id or issuer.rstrip("/") != expected_issuer:
        raise ValidationError({"mailbox": "Microsoft returned an invalid identity issuer."})
    address = claims.get("email") or claims.get("preferred_username")
    account_type = "consumer" if tenant_id == OUTLOOK_CONSUMER_TENANT_ID else "microsoft365"
    return {
        "address": _validated_mailbox_address(address),
        "display_name": str(claims.get("name") or "").strip(),
        "account_type": account_type,
    }


def resolve_email_oauth_identity(
    provider_key: str,
    token_payload: dict[str, Any],
    client_id: str,
) -> dict[str, str]:
    provider = canonical_email_provider(provider_key)
    if provider == "gmail":
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValidationError({"mailbox": "Google did not return an access token."})
        return _gmail_identity(access_token)
    if provider == "outlook":
        return _outlook_identity(str(token_payload.get("id_token") or "").strip(), client_id)
    raise ValidationError({"provider": "This provider cannot be connected as agent email."})


def _provider_transport(provider_key: str, account_type: str) -> dict[str, Any]:
    if provider_key == "gmail":
        return {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_security": AgentEmailAccount.SmtpSecurity.STARTTLS,
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "imap_security": AgentEmailAccount.ImapSecurity.SSL,
        }
    smtp_host = "smtp-mail.outlook.com" if account_type == "consumer" else "smtp.office365.com"
    return {
        "smtp_host": smtp_host,
        "smtp_port": 587,
        "smtp_security": AgentEmailAccount.SmtpSecurity.STARTTLS,
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "imap_security": AgentEmailAccount.ImapSecurity.SSL,
    }


def _retained_custom_transport(account: AgentEmailAccount) -> dict[str, Any]:
    return {
        "smtp_host": account.smtp_host,
        "smtp_port": account.smtp_port,
        "smtp_security": account.smtp_security,
        "smtp_auth": account.smtp_auth,
        "smtp_username": account.smtp_username,
        "imap_host": account.imap_host,
        "imap_port": account.imap_port,
        "imap_security": account.imap_security,
        "imap_auth": account.imap_auth,
        "imap_username": account.imap_username,
        "imap_folder": account.imap_folder,
        "imap_idle_enabled": account.imap_idle_enabled,
        "poll_interval_sec": account.poll_interval_sec,
    }


def _restore_retained_custom_transport(account: AgentEmailAccount, metadata: dict[str, Any]) -> None:
    retained = metadata.get("retained_custom_transport")
    if not isinstance(retained, dict):
        return
    allowed_fields = {
        "smtp_host", "smtp_port", "smtp_security", "smtp_auth", "smtp_username",
        "imap_host", "imap_port", "imap_security", "imap_auth", "imap_username",
        "imap_folder", "imap_idle_enabled", "poll_interval_sec",
    }
    for field, value in retained.items():
        if field in allowed_fields:
            setattr(account, field, value)
    account.connection_mode = AgentEmailAccount.ConnectionMode.CUSTOM
    account.is_outbound_enabled = False
    account.is_inbound_enabled = False
    account.save()


@transaction.atomic
def connect_agent_email_oauth(
    *,
    agent: PersistentAgent,
    provider_key: str,
    identity: dict[str, str],
    token_payload: dict[str, Any],
    client_id: str,
    client_secret: str,
    user,
    organization,
    token_endpoint: str,
    requested_scope: str,
) -> AgentEmailAccount:
    from api.services.persistent_agents import ensure_default_agent_email_endpoint

    provider = canonical_email_provider(provider_key)
    if provider not in EMAIL_NATIVE_PROVIDER_KEYS:
        raise ValidationError({"provider": "Unsupported email provider."})

    mailbox = _validated_mailbox_address(identity.get("address"))
    default_endpoint = ensure_default_agent_email_endpoint(agent, is_primary=False)
    endpoint = PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.EMAIL,
        address__iexact=mailbox,
    ).first()
    if endpoint is not None and endpoint.owner_agent_id not in (None, agent.id):
        raise ValidationError({"mailbox": "That mailbox is already connected to another agent."})
    if endpoint is None:
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=mailbox,
            is_primary=True,
        )
    else:
        endpoint.owner_agent = agent
        endpoint.is_primary = True
        endpoint.save(update_fields=["owner_agent", "is_primary"])
    agent.comms_endpoints.filter(channel=CommsChannel.EMAIL).exclude(pk=endpoint.pk).update(is_primary=False)

    email_meta, _ = PersistentAgentEmailEndpoint.objects.get_or_create(endpoint=endpoint)
    display_name = str(identity.get("display_name") or "").strip()
    if display_name and not email_meta.display_name:
        email_meta.display_name = display_name
        email_meta.save(update_fields=["display_name"])

    integration = get_or_create_agent_email_integration(agent)
    previous_oauth_account = integration.oauth_account
    existing_account = AgentEmailAccount.objects.filter(endpoint=endpoint).first()
    account = existing_account or AgentEmailAccount(endpoint=endpoint)
    credential = oauth_credential_for_account(account) if account.pk else None
    existing_refresh_token = credential.refresh_token if credential else ""
    existing_metadata = dict(credential.metadata) if credential and isinstance(credential.metadata, dict) else {}
    if existing_account and integration.custom_account_id == existing_account.pk:
        existing_metadata.setdefault("retained_custom_transport", _retained_custom_transport(existing_account))

    for field, value in _provider_transport(provider, identity.get("account_type", "")).items():
        setattr(account, field, value)
    account.smtp_auth = AgentEmailAccount.AuthMode.OAUTH2
    account.imap_auth = AgentEmailAccount.ImapAuthMode.OAUTH2
    account.smtp_username = mailbox
    account.imap_username = mailbox
    account.connection_mode = AgentEmailAccount.ConnectionMode.OAUTH2
    account.is_outbound_enabled = True
    account.is_inbound_enabled = True
    account.smtp_error = ""
    account.imap_error = ""
    account.save()

    credential, _ = AgentEmailOAuthCredential.objects.get_or_create(
        account=account,
        defaults={"user": user, "organization": organization},
    )
    credential.user = user
    credential.organization = organization
    credential.provider = provider
    credential.client_id = client_id
    credential.client_secret = client_secret
    credential.access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if refresh_token or not existing_refresh_token:
        credential.refresh_token = refresh_token
    id_token = str(token_payload.get("id_token") or "").strip()
    if id_token:
        credential.id_token = id_token
    credential.token_type = str(token_payload.get("token_type") or "Bearer").strip()
    credential.scope = str(token_payload.get("scope") or requested_scope).strip()
    try:
        expires_in = int(token_payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    credential.expires_at = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
    credential.metadata = {
        **existing_metadata,
        "token_endpoint": token_endpoint,
        "account_type": identity.get("account_type", ""),
        "mailbox_address": mailbox,
        "sasl_mechanism": "XOAUTH2",
        "transport": (
            "gmail_api"
            if provider == "gmail" and "https://mail.google.com/" not in credential.scope.split()
            else "smtp_imap"
        ),
    }
    credential.save()

    if previous_oauth_account and previous_oauth_account.pk != account.pk:
        previous_endpoint = previous_oauth_account.endpoint
        previous_credential = oauth_credential_for_account(previous_oauth_account)
        previous_metadata = (
            dict(previous_credential.metadata)
            if previous_credential and isinstance(previous_credential.metadata, dict)
            else {}
        )
        if previous_credential:
            previous_credential.delete()
        if integration.custom_account_id == previous_oauth_account.pk:
            _restore_retained_custom_transport(previous_oauth_account, previous_metadata)
        else:
            previous_oauth_account.delete()
            previous_endpoint.delete()

    integration.oauth_account = account
    integration.active_mode = AgentEmailIntegration.ActiveMode.OAUTH
    integration.save(update_fields=["oauth_account", "active_mode", "updated_at"])
    if default_endpoint and default_endpoint.is_primary:
        default_endpoint.is_primary = False
        default_endpoint.save(update_fields=["is_primary"])
    return account


@transaction.atomic
def disconnect_agent_email_oauth(agent: PersistentAgent, provider_key: str) -> bool:
    from api.services.persistent_agents import ensure_default_agent_email_endpoint

    integration = get_or_create_agent_email_integration(agent)
    account = integration.oauth_account
    credential = oauth_credential_for_account(account)
    if account is None or credential is None:
        return False
    expected = canonical_email_provider(provider_key)
    if expected and canonical_email_provider(credential.provider) != expected:
        raise ValidationError({"provider": "That provider is not connected to this agent."})

    same_as_custom = integration.custom_account_id == account.pk
    endpoint = account.endpoint
    metadata = dict(credential.metadata) if isinstance(credential.metadata, dict) else {}
    credential.delete()
    if same_as_custom:
        _restore_retained_custom_transport(account, metadata)
    else:
        account.delete()
        endpoint.delete()

    integration.oauth_account = None
    integration.active_mode = AgentEmailIntegration.ActiveMode.NONE
    integration.save(update_fields=["oauth_account", "active_mode", "updated_at"])
    default_endpoint = ensure_default_agent_email_endpoint(agent, is_primary=True)
    if default_endpoint:
        default_endpoint.is_primary = True
        default_endpoint.save(update_fields=["is_primary"])
    return True


def serialize_agent_email_connection(agent: PersistentAgent, provider_key: str = "") -> dict[str, Any]:
    integration = AgentEmailIntegration.objects.filter(agent=agent).select_related(
        "oauth_account__endpoint", "oauth_account__oauth_credential"
    ).first()
    oauth_account = integration.oauth_account if integration else None
    credential = oauth_credential_for_account(oauth_account)
    connected_provider = canonical_email_provider(credential.provider) if credential else ""
    requested_provider = canonical_email_provider(provider_key)
    connected = bool(
        credential
        and integration is not None
        and integration.active_mode == AgentEmailIntegration.ActiveMode.OAUTH
        and (not requested_provider or connected_provider == requested_provider)
    )
    default_endpoint = get_default_agent_email_endpoint(agent)
    return {
        "agent_id": str(agent.pk),
        "agent_name": agent.name,
        "provider": connected_provider,
        "mailbox_address": oauth_account.endpoint.address if oauth_account else "",
        "connected": connected,
        "active_mode": integration.active_mode if integration else AgentEmailIntegration.ActiveMode.NONE,
        "send_enabled": bool(
            integration
            and integration.active_mode == AgentEmailIntegration.ActiveMode.OAUTH
            and oauth_account
            and oauth_account.is_outbound_enabled
        ),
        "receive_enabled": bool(
            integration
            and integration.active_mode == AgentEmailIntegration.ActiveMode.OAUTH
            and oauth_account
            and oauth_account.is_inbound_enabled
        ),
        "smtp_last_ok_at": oauth_account.smtp_last_ok_at.isoformat() if oauth_account and oauth_account.smtp_last_ok_at else None,
        "smtp_error": oauth_account.smtp_error if oauth_account else "",
        "imap_last_ok_at": oauth_account.imap_last_ok_at.isoformat() if oauth_account and oauth_account.imap_last_ok_at else None,
        "imap_error": oauth_account.imap_error if oauth_account else "",
        "gobii_address": default_endpoint.address if default_endpoint else "",
    }
