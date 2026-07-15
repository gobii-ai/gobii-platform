import ast
import copy
import logging
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.conf import settings
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views import View

from api.encryption import SecretsEncryption
from api.models import AgentEmailAccount, AgentEmailIntegration, AgentEmailOAuthCredential, CommsChannel, PersistentAgent, PersistentAgentCommsEndpoint, PersistentAgentEmailEndpoint
from api.services.agent_email_integrations import canonical_email_provider, disconnect_agent_email_oauth, get_or_create_agent_email_integration, oauth_credential_for_account
from api.services.agent_email_aliases import get_default_agent_email_domain, get_default_agent_email_endpoint, is_default_agent_email_address
from api.services.persistent_agents import ensure_default_agent_email_endpoint
from console.api_helpers import ApiLoginRequiredMixin, _coerce_bool, _parse_json_body
from util.urls import IMMERSIVE_APP_BASE_PATH
from console.email_settings.constants import EMAIL_OAUTH_PROVIDER_DEFAULTS
from console.agent_chat.access import resolve_manageable_agent_for_request
from console.forms import AgentEmailAccountConsoleForm
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


logger = logging.getLogger(__name__)

EMAIL_ENDPOINT_REQUIRED_ERROR = "Please provide a valid email address."
EMAIL_ENDPOINT_CONFLICT_ERROR = "That email address is already assigned to another agent."
AGENT_EMAIL_ACCOUNT_COPY_EXCLUDED_FIELDS = {"endpoint", "created_at", "updated_at"}
AGENT_EMAIL_ACCOUNT_PASSWORD_INPUT_FIELDS = {"smtp_password", "imap_password"}
SMTP_CONNECTION_FIELDS = (
    "smtp_host",
    "smtp_port",
    "smtp_security",
    "smtp_auth",
    "smtp_username",
)
IMAP_CONNECTION_FIELDS = (
    "imap_host",
    "imap_port",
    "imap_security",
    "imap_auth",
    "imap_username",
    "imap_folder",
)
AGENT_EMAIL_ACCOUNT_COPY_FIELDS = tuple(
    field.name
    for field in AgentEmailAccount._meta.concrete_fields
    if field.name not in AGENT_EMAIL_ACCOUNT_COPY_EXCLUDED_FIELDS
)


def _resolve_owned_agent_for_email_settings(request: HttpRequest, agent_id: str) -> PersistentAgent:
    return resolve_manageable_agent_for_request(
        request,
        agent_id,
    )


def _get_agent_email_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint | None:
    endpoint = agent.comms_endpoints.filter(
        channel=CommsChannel.EMAIL,
        owner_agent=agent,
        is_primary=True,
    ).first()
    if endpoint:
        return endpoint
    endpoint_with_account = (
        agent.comms_endpoints
        .filter(channel=CommsChannel.EMAIL, owner_agent=agent, agentemailaccount__isnull=False)
        .order_by("-is_primary", "address")
        .first()
    )
    if endpoint_with_account:
        return endpoint_with_account
    return agent.comms_endpoints.filter(
        channel=CommsChannel.EMAIL,
        owner_agent=agent,
    ).first()


def _copy_agent_email_account_data(source: AgentEmailAccount, target: AgentEmailAccount) -> None:
    for field in AGENT_EMAIL_ACCOUNT_COPY_FIELDS:
        setattr(target, field, getattr(source, field))


def _sync_oauth_usernames_to_endpoint(
    account: AgentEmailAccount,
    endpoint_address: str,
    previous_endpoint_address: str = "",
    force: bool = False,
) -> None:
    if account.connection_mode != AgentEmailAccount.ConnectionMode.OAUTH2:
        return

    current_address = (endpoint_address or "").strip()
    previous_address = (previous_endpoint_address or "").strip()
    if not current_address:
        return

    for field in ("smtp_username", "imap_username"):
        username = (getattr(account, field) or "").strip()
        if force or not username or (previous_address and username.casefold() == previous_address.casefold()):
            setattr(account, field, current_address)


def _validate_and_normalize_email_endpoint_address(endpoint_address: str) -> str:
    raw_address = (endpoint_address or "").strip()
    if not raw_address:
        raise ValidationError({"endpoint_address": EMAIL_ENDPOINT_REQUIRED_ERROR})
    try:
        validate_email(raw_address)
    except ValidationError as exc:
        raise ValidationError({"endpoint_address": EMAIL_ENDPOINT_REQUIRED_ERROR}) from exc

    normalized = PersistentAgentCommsEndpoint.normalize_address(
        CommsChannel.EMAIL,
        raw_address,
    )
    if not normalized:
        raise ValidationError({"endpoint_address": EMAIL_ENDPOINT_REQUIRED_ERROR})
    return normalized


def _save_agent_email_endpoint_updates(
    endpoint: PersistentAgentCommsEndpoint,
    agent: PersistentAgent,
    normalized_address: str,
) -> None:
    updates = []
    if endpoint.owner_agent_id != agent.id:
        endpoint.owner_agent = agent
        updates.append("owner_agent")
    if endpoint.address != normalized_address:
        endpoint.address = normalized_address
        updates.append("address")
    if not endpoint.is_primary:
        endpoint.is_primary = True
        updates.append("is_primary")
    if updates:
        endpoint.save(update_fields=updates)


def _resolve_or_create_agent_email_endpoint(
    agent: PersistentAgent,
    current_endpoint: PersistentAgentCommsEndpoint | None,
    normalized_address: str,
) -> PersistentAgentCommsEndpoint:
    existing_endpoint = PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.EMAIL,
        address__iexact=normalized_address,
    ).first()

    if existing_endpoint and existing_endpoint.owner_agent_id and existing_endpoint.owner_agent_id != agent.id:
        raise ValidationError({"endpoint_address": EMAIL_ENDPOINT_CONFLICT_ERROR})

    if not current_endpoint:
        if existing_endpoint:
            _save_agent_email_endpoint_updates(existing_endpoint, agent, normalized_address)
            return existing_endpoint
        return PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=normalized_address,
            is_primary=True,
        )

    if existing_endpoint and existing_endpoint.id != current_endpoint.id:
        if current_endpoint.is_primary:
            current_endpoint.is_primary = False
            current_endpoint.save(update_fields=["is_primary"])
        _save_agent_email_endpoint_updates(existing_endpoint, agent, normalized_address)
        return existing_endpoint

    if (
        current_endpoint.address != normalized_address
        and is_default_agent_email_address(current_endpoint.address)
    ):
        if current_endpoint.is_primary:
            current_endpoint.is_primary = False
            current_endpoint.save(update_fields=["is_primary"])
        return PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=normalized_address,
            is_primary=True,
        )

    _save_agent_email_endpoint_updates(current_endpoint, agent, normalized_address)
    return current_endpoint


def _move_agent_email_account_data(source: AgentEmailAccount, target: AgentEmailAccount) -> None:
    _copy_agent_email_account_data(source, target)
    _sync_oauth_usernames_to_endpoint(target, target.endpoint.address, source.endpoint.address)
    target.save()
    try:
        credential = source.oauth_credential
    except AgentEmailOAuthCredential.DoesNotExist:
        credential = None
    if credential:
        credential.account = target
        credential.save(update_fields=["account"])
    source.delete()


def _ensure_agent_email_endpoint_and_account(
    agent: PersistentAgent,
    endpoint_address: str,
) -> tuple[PersistentAgentCommsEndpoint, AgentEmailAccount, bool]:
    normalized_address = _validate_and_normalize_email_endpoint_address(endpoint_address)

    with transaction.atomic():
        if settings.ENABLE_DEFAULT_AGENT_EMAIL:
            ensure_default_agent_email_endpoint(agent, is_primary=False)

        integration = get_or_create_agent_email_integration(agent)
        if integration.custom_account_id:
            current_endpoint = integration.custom_account.endpoint
            existing_account = integration.custom_account
        else:
            current_endpoint = _get_agent_email_endpoint(agent)
            existing_account = getattr(current_endpoint, "agentemailaccount", None) if current_endpoint else None

        endpoint = _resolve_or_create_agent_email_endpoint(
            agent,
            current_endpoint,
            normalized_address,
        )

        new_account, created = AgentEmailAccount.objects.get_or_create(
            endpoint=endpoint,
            defaults={"imap_idle_enabled": True},
        )
        if existing_account and existing_account.pk != new_account.pk:
            _move_agent_email_account_data(existing_account, new_account)

    return endpoint, new_account, created


def _apply_email_account_settings(
    account: AgentEmailAccount,
    endpoint: PersistentAgentCommsEndpoint,
    cleaned_data: dict[str, Any],
    provider: str = "",
    previous_endpoint_address: str = "",
) -> None:
    for field, value in cleaned_data.items():
        if field in AGENT_EMAIL_ACCOUNT_PASSWORD_INPUT_FIELDS:
            continue
        if hasattr(account, field):
            setattr(account, field, value)

    smtp_password = cleaned_data.get("smtp_password")
    if smtp_password:
        account.smtp_password_encrypted = SecretsEncryption.encrypt_value(smtp_password)
    imap_password = cleaned_data.get("imap_password")
    if imap_password:
        account.imap_password_encrypted = SecretsEncryption.encrypt_value(imap_password)

    if account.connection_mode == AgentEmailAccount.ConnectionMode.OAUTH2:
        account.smtp_auth = AgentEmailAccount.AuthMode.OAUTH2
        account.imap_auth = AgentEmailAccount.ImapAuthMode.OAUTH2

        provider_key = (provider or "").lower()
        if not provider_key:
            try:
                provider_key = (account.oauth_credential.provider or "").lower()
            except AgentEmailOAuthCredential.DoesNotExist:
                provider_key = ""
        _sync_oauth_usernames_to_endpoint(
            account,
            endpoint.address,
            previous_endpoint_address,
            force=provider_key in EMAIL_OAUTH_PROVIDER_DEFAULTS,
        )
        defaults = EMAIL_OAUTH_PROVIDER_DEFAULTS.get(provider_key)
        if defaults:
            for key, value in defaults.items():
                setattr(account, key, value)


def _validate_agent_smtp_connection(account: AgentEmailAccount) -> tuple[bool, str]:
    from api.agent.comms.gmail_api import GmailApiError, uses_gmail_api, validate_gmail_send_access

    if uses_gmail_api(account):
        try:
            validate_gmail_send_access(account)
            return True, ""
        except GmailApiError as exc:
            logger.warning(
                "Gmail API send validation failed for agent email account %s: %s",
                account.pk,
                exc,
            )
            return False, str(exc)

    try:
        import smtplib

        if account.smtp_security == AgentEmailAccount.SmtpSecurity.SSL:
            client = smtplib.SMTP_SSL(account.smtp_host, int(account.smtp_port or 465), timeout=30)
        else:
            client = smtplib.SMTP(account.smtp_host, int(account.smtp_port or 587), timeout=30)
        try:
            client.ehlo()
            if account.smtp_security == AgentEmailAccount.SmtpSecurity.STARTTLS:
                client.starttls()
                client.ehlo()
            if account.smtp_auth == AgentEmailAccount.AuthMode.OAUTH2:
                from api.agent.comms.email_oauth import build_xoauth2_string, resolve_oauth_identity_and_token

                identity, access_token, _credential = resolve_oauth_identity_and_token(account, "smtp")
                auth_string = build_xoauth2_string(identity, access_token)
                client.auth("XOAUTH2", lambda _=None: auth_string)
            elif account.smtp_auth != AgentEmailAccount.AuthMode.NONE:
                client.login(account.smtp_username or "", account.get_smtp_password() or "")
            try:
                client.noop()
            except Exception as exc:
                logger.debug("SMTP noop failed during connection test cleanup: %s", exc, exc_info=exc)
        finally:
            try:
                client.quit()
            except Exception as exc:
                logger.debug("SMTP quit failed during connection test cleanup: %s", exc, exc_info=exc)
                try:
                    client.close()
                except Exception as close_exc:
                    logger.debug("SMTP close failed during connection test cleanup: %s", close_exc, exc_info=close_exc)
        return True, ""
    except Exception as exc:
        logger.warning(
            "SMTP connection test failed for agent email account %s endpoint %s provider %s auth %s: %r",
            account.pk,
            account.endpoint_id,
            _email_oauth_provider(account),
            account.smtp_auth,
            exc,
            exc_info=exc,
        )
        return False, _format_email_connection_error(
            exc,
            channel="smtp",
            auth_mode=account.smtp_auth,
            provider=_email_oauth_provider(account),
        )


def _validate_agent_imap_connection(account: AgentEmailAccount) -> tuple[bool, str]:
    from api.agent.comms.gmail_api import GmailApiError, uses_gmail_api, validate_gmail_receive_access

    if uses_gmail_api(account):
        try:
            validate_gmail_receive_access(account)
            return True, ""
        except GmailApiError as exc:
            logger.warning(
                "Gmail API receive validation failed for agent email account %s: %s",
                account.pk,
                exc,
            )
            return False, str(exc)

    try:
        import imaplib

        if account.imap_security == AgentEmailAccount.ImapSecurity.SSL:
            client = imaplib.IMAP4_SSL(account.imap_host, int(account.imap_port or 993), timeout=30)
        else:
            client = imaplib.IMAP4(account.imap_host, int(account.imap_port or 143), timeout=30)
            if account.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
                client.starttls()
        try:
            if account.imap_auth == AgentEmailAccount.ImapAuthMode.OAUTH2:
                from api.agent.comms.email_oauth import build_xoauth2_string, resolve_oauth_identity_and_token

                identity, access_token, _credential = resolve_oauth_identity_and_token(account, "imap")
                auth_string = build_xoauth2_string(identity, access_token)
                client.authenticate("XOAUTH2", lambda _: auth_string.encode("utf-8"))
            elif account.imap_auth != AgentEmailAccount.ImapAuthMode.NONE:
                client.login(account.imap_username or "", account.get_imap_password() or "")
            client.select(account.imap_folder or "INBOX", readonly=True)
            try:
                client.noop()
            except Exception as exc:
                logger.debug("IMAP noop failed during connection test cleanup: %s", exc, exc_info=exc)
        finally:
            try:
                client.logout()
            except Exception as exc:
                logger.debug("IMAP logout failed during connection test cleanup: %s", exc, exc_info=exc)
                try:
                    client.shutdown()
                except Exception as shutdown_exc:
                    logger.debug(
                        "IMAP shutdown failed during connection test cleanup: %s",
                        shutdown_exc,
                        exc_info=shutdown_exc,
                    )
        return True, ""
    except Exception as exc:
        logger.warning(
            "IMAP connection test failed for agent email account %s endpoint %s provider %s auth %s: %r",
            account.pk,
            account.endpoint_id,
            _email_oauth_provider(account),
            account.imap_auth,
            exc,
            exc_info=exc,
        )
        return False, _format_email_connection_error(
            exc,
            channel="imap",
            auth_mode=account.imap_auth,
            provider=_email_oauth_provider(account),
        )


def _decode_email_error_part(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="ignore").strip()
    return str(value).strip()


def _normalize_email_error_text(raw_error: Any) -> str:
    text = str(raw_error or "").strip()
    if not text:
        return ""

    # smtplib often raises tuples like "(535, b'...')"; parse and flatten.
    if text.startswith("(") and text.endswith(")"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, tuple):
            flattened = " ".join(_decode_email_error_part(part) for part in parsed if part is not None).strip()
            if flattened:
                text = flattened

    # imaplib can return bytes repr strings like "b'Empty username or password...'"
    if (text.startswith("b'") and text.endswith("'")) or (text.startswith('b"') and text.endswith('"')):
        try:
            parsed_bytes = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed_bytes = None
        if isinstance(parsed_bytes, (bytes, bytearray)):
            text = parsed_bytes.decode("utf-8", errors="ignore").strip()

    text = text.replace("\\r", " ").replace("\\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip(" '\"")
    return text


def _email_oauth_provider(account: AgentEmailAccount) -> str:
    try:
        return (account.oauth_credential.provider or "").strip().lower()
    except AgentEmailOAuthCredential.DoesNotExist:
        return ""


def _format_email_connection_error(
    raw_error: Any,
    *,
    channel: str = "",
    auth_mode: str = "",
    provider: str = "",
) -> str:
    normalized = _normalize_email_error_text(raw_error)
    lowered = normalized.lower()
    channel_key = channel.strip().lower()
    auth_key = auth_mode.strip().lower()
    provider_key = provider.strip().lower()
    is_oauth = auth_key == "oauth2"
    is_microsoft = provider_key in {"microsoft", "outlook", "o365", "office365"}
    if "empty username or password" in lowered:
        return "Username or password is missing. Enter both values and try again."
    if "smtpclientauthentication is disabled for the mailbox" in lowered:
        return "Microsoft says SMTP AUTH is disabled for this mailbox. Enable authenticated SMTP for the mailbox, or use a different outbound mail provider."
    if "smtpclientauthentication is disabled for the tenant" in lowered or "smtp auth is disabled" in lowered:
        return "Microsoft says SMTP AUTH is disabled for this tenant. Enable authenticated SMTP, or use a different outbound mail provider."
    if "user is authenticated but not connected" in lowered or "5.7.139" in lowered:
        return "Microsoft accepted the sign-in but blocked SMTP AUTH for this mailbox. Enable authenticated SMTP for the mailbox, or use a different outbound mail provider."
    if (
        "imap is disabled" in lowered
        or "pop is disabled" in lowered
        or "application-specific password required" in lowered
    ):
        return "IMAP access is disabled for this mailbox. Enable IMAP for the account and try again."
    if (
        "username and password not accepted" in lowered
        or "badcredentials" in lowered
        or "authentication failed" in lowered
        or "invalid credentials" in lowered
        or "5.7.3 authentication unsuccessful" in lowered
        or "535 5.7.3" in lowered
    ):
        if is_oauth:
            if is_microsoft and channel_key == "smtp":
                return "Microsoft rejected SMTP OAuth for this mailbox. Confirm Authenticated SMTP is enabled for the mailbox and try reconnecting OAuth."
            if is_microsoft and channel_key == "imap":
                return "Microsoft rejected IMAP OAuth for this mailbox. Confirm IMAP access is enabled for the mailbox and try reconnecting OAuth."
            return "OAuth authentication failed. Reconnect this email account and try again."
        return "Authentication failed. Check your username and password. For Gmail manual setup, use an app password."
    return normalized or "Connection test failed."


def _is_first_time_custom_email_setup(
    account: AgentEmailAccount | None,
    credential: AgentEmailOAuthCredential | None,
) -> bool:
    if not account:
        return False

    has_manual_transport_config = any(
        (
            bool(account.smtp_host),
            bool(account.imap_host),
            bool(account.smtp_username),
            bool(account.imap_username),
            bool(account.smtp_password_encrypted),
            bool(account.imap_password_encrypted),
        )
    )
    has_runtime_connection_state = any(
        (
            bool(account.connection_error),
            account.connection_last_ok_at is not None,
            account.last_polled_at is not None,
            bool(account.last_seen_uid),
            account.backoff_until is not None,
        )
    )
    has_nondefault_inbound_config = (account.imap_folder or "INBOX").upper() != "INBOX"
    has_nondefault_polling = account.poll_interval_sec != 120
    has_direction_enabled = account.is_outbound_enabled or account.is_inbound_enabled

    return not any(
        (
            has_manual_transport_config,
            has_runtime_connection_state,
            has_nondefault_inbound_config,
            has_nondefault_polling,
            has_direction_enabled,
            credential is not None,
        )
    )


def _endpoint_display_name(endpoint: PersistentAgentCommsEndpoint | None) -> str:
    if endpoint is None:
        return ""
    try:
        return endpoint.email_meta.display_name
    except PersistentAgentEmailEndpoint.DoesNotExist:
        return ""


def _serialize_agent_email_settings(
    request: HttpRequest,
    agent: PersistentAgent,
    endpoint: PersistentAgentCommsEndpoint | None,
    account: AgentEmailAccount | None,
) -> dict[str, Any]:
    integration = get_or_create_agent_email_integration(agent)
    if integration.active_mode == AgentEmailIntegration.ActiveMode.OAUTH:
        account = integration.oauth_account
    elif integration.active_mode == AgentEmailIntegration.ActiveMode.CUSTOM:
        account = integration.custom_account
        if account is None:
            endpoint = None
    else:
        account = None
    endpoint = account.endpoint if account else endpoint
    credential = oauth_credential_for_account(integration.oauth_account)
    configured_display_name = _endpoint_display_name(endpoint)
    if account and not configured_display_name:
        configured_display_name = (agent.name or "").strip() or endpoint.address.partition("@")[0]

    is_first_time_custom_setup = _is_first_time_custom_email_setup(account, credential)

    imap_idle_enabled = True
    if account and not is_first_time_custom_setup:
        imap_idle_enabled = bool(account.imap_idle_enabled)

    endpoint_payload = {
        "address": endpoint.address if endpoint else "",
        "exists": endpoint is not None,
        "displayName": configured_display_name,
        "readOnly": integration.active_mode == AgentEmailIntegration.ActiveMode.OAUTH,
    }
    default_endpoint = get_default_agent_email_endpoint(agent)
    default_endpoint_payload = {
        "address": default_endpoint.address if default_endpoint else "",
        "exists": default_endpoint is not None,
        "isInboundAliasActive": default_endpoint is not None,
        "displayName": _endpoint_display_name(default_endpoint),
    }
    account_payload = {
        "id": str(account.pk) if account else None,
        "exists": account is not None,
        "smtpHost": account.smtp_host if account else "",
        "smtpPort": account.smtp_port if account else None,
        "smtpSecurity": account.smtp_security if account else AgentEmailAccount.SmtpSecurity.STARTTLS,
        "smtpAuth": account.smtp_auth if account else AgentEmailAccount.AuthMode.LOGIN,
        "smtpUsername": account.smtp_username if account else "",
        "hasSmtpPassword": bool(account and account.smtp_password_encrypted),
        "imapHost": account.imap_host if account else "",
        "imapPort": account.imap_port if account else None,
        "imapSecurity": account.imap_security if account else AgentEmailAccount.ImapSecurity.SSL,
        "imapAuth": account.imap_auth if account else AgentEmailAccount.ImapAuthMode.LOGIN,
        "imapUsername": account.imap_username if account else "",
        "hasImapPassword": bool(account and account.imap_password_encrypted),
        "imapFolder": account.imap_folder if account else "INBOX",
        "isOutboundEnabled": bool(account.is_outbound_enabled) if account else False,
        "isInboundEnabled": bool(account.is_inbound_enabled) if account else False,
        "imapIdleEnabled": imap_idle_enabled,
        "pollIntervalSec": account.poll_interval_sec if account else 120,
        "connectionMode": account.connection_mode if account else AgentEmailAccount.ConnectionMode.CUSTOM,
        "connectionLastOkAt": account.connection_last_ok_at.isoformat() if account and account.connection_last_ok_at else None,
        "connectionError": account.connection_error if account else "",
        "smtpLastOkAt": account.smtp_last_ok_at.isoformat() if account and account.smtp_last_ok_at else None,
        "smtpError": account.smtp_error if account else "",
        "imapLastOkAt": account.imap_last_ok_at.isoformat() if account and account.imap_last_ok_at else None,
        "imapError": account.imap_error if account else "",
    }

    oauth_provider = canonical_email_provider(credential.provider) if credential else ""
    oauth_payload = {
        "connected": credential is not None,
        "provider": oauth_provider,
        "legacy": bool(credential and oauth_provider not in {"gmail", "outlook"}),
        "scope": credential.scope if credential else "",
        "expiresAt": credential.expires_at.isoformat() if credential and credential.expires_at else None,
        "mailboxAddress": integration.oauth_account.endpoint.address if integration.oauth_account else "",
        "callbackPath": reverse("console-native-integration-oauth-callback-view"),
        "startUrl": reverse("console-native-integration-connect", args=[oauth_provider]) if oauth_provider in {"gmail", "outlook"} else None,
        "statusUrl": None,
        "revokeUrl": (
            reverse("console-native-integration-revoke", args=[oauth_provider])
            if oauth_provider in {"gmail", "outlook"}
            else request.path if credential else None
        ),
        "gmailConnectUrl": reverse("console-native-integration-connect", args=["gmail"]),
        "gmailRevokeUrl": reverse("console-native-integration-revoke", args=["gmail"]),
        "outlookConnectUrl": reverse("console-native-integration-connect", args=["outlook"]),
        "outlookRevokeUrl": reverse("console-native-integration-revoke", args=["outlook"]),
    }

    return {
        "agent": {
            "id": str(agent.pk),
            "name": agent.name,
            "backUrl": f"{IMMERSIVE_APP_BASE_PATH}/agents/{agent.pk}/settings",
            "helpUrl": "https://docs.gobii.ai/advanced-usage/custom-email-settings",
        },
        "providerDefaults": EMAIL_OAUTH_PROVIDER_DEFAULTS,
        "defaultEmailDomain": get_default_agent_email_domain(),
        "endpoint": endpoint_payload,
        "defaultEndpoint": default_endpoint_payload,
        "account": account_payload,
        "oauth": oauth_payload,
        "activeMode": integration.active_mode,
        "customConfigured": integration.custom_account_id is not None,
        "customEnabled": integration.active_mode == AgentEmailIntegration.ActiveMode.CUSTOM,
    }


def _email_settings_payload_value(payload: dict[str, Any], camel_key: str, snake_key: str, default: Any = None) -> Any:
    if camel_key in payload:
        return payload.get(camel_key)
    if snake_key in payload:
        return payload.get(snake_key)
    return default


def _build_email_settings_form_input(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "smtp_host": _email_settings_payload_value(payload, "smtpHost", "smtp_host", ""),
        "smtp_port": _email_settings_payload_value(payload, "smtpPort", "smtp_port"),
        "smtp_security": _email_settings_payload_value(
            payload,
            "smtpSecurity",
            "smtp_security",
            AgentEmailAccount.SmtpSecurity.STARTTLS,
        ),
        "smtp_auth": _email_settings_payload_value(
            payload,
            "smtpAuth",
            "smtp_auth",
            AgentEmailAccount.AuthMode.LOGIN,
        ),
        "smtp_username": _email_settings_payload_value(payload, "smtpUsername", "smtp_username", ""),
        "smtp_password": _email_settings_payload_value(payload, "smtpPassword", "smtp_password", ""),
        "is_outbound_enabled": _coerce_bool(
            _email_settings_payload_value(payload, "isOutboundEnabled", "is_outbound_enabled", False)
        ),
        "imap_host": _email_settings_payload_value(payload, "imapHost", "imap_host", ""),
        "imap_port": _email_settings_payload_value(payload, "imapPort", "imap_port"),
        "imap_security": _email_settings_payload_value(
            payload,
            "imapSecurity",
            "imap_security",
            AgentEmailAccount.ImapSecurity.SSL,
        ),
        "imap_username": _email_settings_payload_value(payload, "imapUsername", "imap_username", ""),
        "imap_password": _email_settings_payload_value(payload, "imapPassword", "imap_password", ""),
        "imap_auth": _email_settings_payload_value(
            payload,
            "imapAuth",
            "imap_auth",
            AgentEmailAccount.ImapAuthMode.LOGIN,
        ),
        "imap_folder": _email_settings_payload_value(payload, "imapFolder", "imap_folder", "INBOX"),
        "is_inbound_enabled": _coerce_bool(
            _email_settings_payload_value(payload, "isInboundEnabled", "is_inbound_enabled", False)
        ),
        "imap_idle_enabled": _coerce_bool(
            _email_settings_payload_value(payload, "imapIdleEnabled", "imap_idle_enabled", True)
        ),
        "poll_interval_sec": _email_settings_payload_value(payload, "pollIntervalSec", "poll_interval_sec", 120),
        "connection_mode": _email_settings_payload_value(
            payload,
            "connectionMode",
            "connection_mode",
            AgentEmailAccount.ConnectionMode.CUSTOM,
        ),
    }


def _build_email_form_error_payload(form: AgentEmailAccountConsoleForm) -> dict[str, list[str]]:
    error_payload: dict[str, list[str]] = {}
    for field, errors in form.errors.items():
        error_payload[field] = [str(err) for err in errors]
    return error_payload


def _expected_email_mode(payload: dict[str, Any]) -> str:
    expected_mode = str(
        _email_settings_payload_value(payload, "expectedActiveMode", "expected_active_mode", "") or ""
    ).strip().lower()
    valid_modes = {choice for choice, _label in AgentEmailIntegration.ActiveMode.choices}
    if expected_mode not in valid_modes:
        raise ValidationError({"expected_active_mode": ["Email connection mode is required."]})
    return expected_mode


def _email_mode_changed_response() -> JsonResponse:
    return JsonResponse(
        {"error": "The email connection changed in another window. Reload the page and try again."},
        status=409,
    )


def _display_name_value(payload: dict[str, Any], camel_key: str, snake_key: str) -> str:
    value = str(_email_settings_payload_value(payload, camel_key, snake_key, "") or "").strip()
    if len(value) > 256:
        raise ValidationError({snake_key: ["Display name must be 256 characters or fewer."]})
    return value


def _save_endpoint_display_name(endpoint: PersistentAgentCommsEndpoint | None, display_name: str) -> None:
    if endpoint is None:
        return
    email_meta, _ = PersistentAgentEmailEndpoint.objects.get_or_create(endpoint=endpoint)
    if email_meta.display_name == display_name:
        return
    email_meta.display_name = display_name
    email_meta.save(update_fields=["display_name"])


def _build_custom_email_draft(
    agent: PersistentAgent,
    integration: AgentEmailIntegration,
    payload: dict[str, Any],
) -> tuple[PersistentAgentCommsEndpoint, AgentEmailAccount, AgentEmailAccount | None, dict[str, Any]]:
    endpoint_address = str(
        _email_settings_payload_value(payload, "endpointAddress", "endpoint_address", "") or ""
    ).strip()
    normalized_address = _validate_and_normalize_email_endpoint_address(endpoint_address)
    conflicting_endpoint = (
        PersistentAgentCommsEndpoint.objects
        .filter(
            channel=CommsChannel.EMAIL,
            address__iexact=normalized_address,
            owner_agent__isnull=False,
        )
        .exclude(owner_agent=agent)
        .first()
    )
    if conflicting_endpoint is not None:
        raise ValidationError({"endpoint_address": [EMAIL_ENDPOINT_CONFLICT_ERROR]})

    saved_account = integration.custom_account
    if saved_account is not None:
        endpoint = copy.copy(saved_account.endpoint)
        endpoint.address = normalized_address
        account = copy.copy(saved_account)
        account.endpoint = endpoint
    else:
        endpoint = PersistentAgentCommsEndpoint(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=normalized_address,
            is_primary=True,
        )
        account = AgentEmailAccount(
            endpoint=endpoint,
            connection_mode=AgentEmailAccount.ConnectionMode.CUSTOM,
            imap_idle_enabled=True,
        )

    form_input = _build_email_settings_form_input(payload)
    form_input["connection_mode"] = AgentEmailAccount.ConnectionMode.CUSTOM
    form = AgentEmailAccountConsoleForm(form_input)
    if not form.is_valid():
        raise ValidationError(_build_email_form_error_payload(form))
    _apply_email_account_settings(
        account,
        endpoint,
        form.cleaned_data,
        previous_endpoint_address=saved_account.endpoint.address if saved_account else "",
    )
    return endpoint, account, saved_account, form.cleaned_data


def _custom_direction_needs_validation(
    draft_account: AgentEmailAccount,
    saved_account: AgentEmailAccount | None,
    *,
    direction: str,
    endpoint_address_changed: bool,
    password_changed: bool,
) -> bool:
    enabled_field = "is_outbound_enabled" if direction == "smtp" else "is_inbound_enabled"
    success_field = "smtp_last_ok_at" if direction == "smtp" else "imap_last_ok_at"
    connection_fields = SMTP_CONNECTION_FIELDS if direction == "smtp" else IMAP_CONNECTION_FIELDS
    if not getattr(draft_account, enabled_field):
        return False
    if saved_account is None or endpoint_address_changed or password_changed:
        return True
    if not getattr(saved_account, enabled_field) or not getattr(saved_account, success_field):
        return True
    return any(
        getattr(draft_account, field) != getattr(saved_account, field)
        for field in connection_fields
    )


def _validate_custom_email_draft(
    draft_account: AgentEmailAccount,
    saved_account: AgentEmailAccount | None,
    payload: dict[str, Any],
    *,
    force_outbound: bool = False,
    force_inbound: bool = False,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    endpoint_address_changed = bool(
        saved_account
        and draft_account.endpoint.address.casefold() != saved_account.endpoint.address.casefold()
    )
    validate_outbound = force_outbound or _custom_direction_needs_validation(
        draft_account,
        saved_account,
        direction="smtp",
        endpoint_address_changed=endpoint_address_changed,
        password_changed=bool(_email_settings_payload_value(payload, "smtpPassword", "smtp_password", "")),
    )
    validate_inbound = force_inbound or _custom_direction_needs_validation(
        draft_account,
        saved_account,
        direction="imap",
        endpoint_address_changed=endpoint_address_changed,
        password_changed=bool(_email_settings_payload_value(payload, "imapPassword", "imap_password", "")),
    )
    results: dict[str, Any] = {"smtp": None, "imap": None}
    errors: dict[str, list[str]] = {}
    now = timezone.now()

    if validate_outbound:
        smtp_ok, smtp_error = _validate_agent_smtp_connection(draft_account)
        results["smtp"] = {"ok": smtp_ok, "error": smtp_error}
        draft_account.smtp_error = smtp_error
        if smtp_ok:
            draft_account.smtp_last_ok_at = now
        else:
            errors["smtp"] = [smtp_error]
    elif not draft_account.is_outbound_enabled:
        draft_account.smtp_error = ""

    if validate_inbound:
        imap_ok, imap_error = _validate_agent_imap_connection(draft_account)
        results["imap"] = {"ok": imap_ok, "error": imap_error}
        draft_account.imap_error = imap_error
        if imap_ok:
            draft_account.imap_last_ok_at = now
        else:
            errors["imap"] = [imap_error]
    elif not draft_account.is_inbound_enabled:
        draft_account.imap_error = ""

    if not errors:
        successful_checks = [
            value
            for value in (draft_account.smtp_last_ok_at, draft_account.imap_last_ok_at)
            if value is not None
        ]
        if successful_checks:
            draft_account.connection_last_ok_at = max(successful_checks)
        draft_account.connection_error = ""
    else:
        draft_account.connection_error = "; ".join(messages[0] for messages in errors.values())
    return results, errors


def _save_agent_email_settings(
    request: HttpRequest,
    agent: PersistentAgent,
    integration: AgentEmailIntegration,
    payload: dict[str, Any],
) -> JsonResponse:
    try:
        expected_mode = _expected_email_mode(payload)
        default_display_name = _display_name_value(payload, "defaultDisplayName", "default_display_name")
        configured_display_name = _display_name_value(payload, "displayName", "display_name")
    except ValidationError as exc:
        return JsonResponse({"errors": exc.message_dict}, status=400)
    if integration.active_mode != expected_mode:
        return _email_mode_changed_response()

    draft_account = None
    cleaned_data = None
    if expected_mode == AgentEmailIntegration.ActiveMode.CUSTOM:
        try:
            _draft_endpoint, draft_account, saved_account, cleaned_data = _build_custom_email_draft(
                agent,
                integration,
                payload,
            )
            _results, validation_errors = _validate_custom_email_draft(
                draft_account,
                saved_account,
                payload,
            )
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)
        if validation_errors:
            return JsonResponse({"errors": validation_errors}, status=400)
    created = False
    with transaction.atomic():
        locked_integration = (
            AgentEmailIntegration.objects
            .select_for_update()
            .select_related("custom_account__endpoint", "oauth_account__endpoint")
            .get(pk=integration.pk)
        )
        if locked_integration.active_mode != expected_mode:
            return _email_mode_changed_response()
        if (
            expected_mode == AgentEmailIntegration.ActiveMode.OAUTH
            and locked_integration.oauth_account is None
        ):
            return _email_mode_changed_response()

        default_endpoint = ensure_default_agent_email_endpoint(
            agent,
            is_primary=expected_mode == AgentEmailIntegration.ActiveMode.NONE,
        )
        _save_endpoint_display_name(default_endpoint, default_display_name)

        endpoint = default_endpoint
        account = None
        if expected_mode == AgentEmailIntegration.ActiveMode.OAUTH:
            account = locked_integration.oauth_account
            account.is_outbound_enabled = _coerce_bool(
                _email_settings_payload_value(
                    payload,
                    "isOutboundEnabled",
                    "is_outbound_enabled",
                    account.is_outbound_enabled,
                )
            )
            account.is_inbound_enabled = _coerce_bool(
                _email_settings_payload_value(
                    payload,
                    "isInboundEnabled",
                    "is_inbound_enabled",
                    account.is_inbound_enabled,
                )
            )
            if not account.is_outbound_enabled:
                account.smtp_error = ""
            if not account.is_inbound_enabled:
                account.imap_error = ""
            account.save(update_fields=[
                "is_outbound_enabled",
                "is_inbound_enabled",
                "smtp_error",
                "imap_error",
                "updated_at",
            ])
            endpoint = account.endpoint
            _save_endpoint_display_name(endpoint, configured_display_name)
        elif expected_mode == AgentEmailIntegration.ActiveMode.CUSTOM:
            endpoint_address = str(
                _email_settings_payload_value(payload, "endpointAddress", "endpoint_address", "") or ""
            ).strip()
            try:
                endpoint, account, created = _ensure_agent_email_endpoint_and_account(agent, endpoint_address)
                _apply_email_account_settings(
                    account,
                    endpoint,
                    cleaned_data or {},
                    previous_endpoint_address=locked_integration.custom_account.endpoint.address
                    if locked_integration.custom_account_id else "",
                )
                for field in (
                    "connection_last_ok_at",
                    "connection_error",
                    "smtp_last_ok_at",
                    "smtp_error",
                    "imap_last_ok_at",
                    "imap_error",
                ):
                    setattr(account, field, getattr(draft_account, field))
                account.full_clean()
                account.save()
            except ValidationError as exc:
                transaction.set_rollback(True)
                return JsonResponse({"errors": exc.message_dict}, status=400)
            _save_endpoint_display_name(endpoint, configured_display_name)
            locked_integration.custom_account = account
            locked_integration.save(update_fields=["custom_account", "updated_at"])

    if expected_mode == AgentEmailIntegration.ActiveMode.CUSTOM:
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.EMAIL_ACCOUNT_CREATED if created else AnalyticsEvent.EMAIL_ACCOUNT_UPDATED,
            source=AnalyticsSource.WEB,
            properties={"agent_id": str(agent.pk), "endpoint": endpoint.address},
        )

    return JsonResponse(
        {
            "ok": True,
            "settings": _serialize_agent_email_settings(request, agent, endpoint, account),
        }
    )


class AgentEmailSettingsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        integration = get_or_create_agent_email_integration(agent)
        ensure_default_agent_email_endpoint(
            agent,
            is_primary=integration.active_mode == AgentEmailIntegration.ActiveMode.NONE,
        )
        endpoint = _get_agent_email_endpoint(agent)
        account = getattr(endpoint, "agentemailaccount", None) if endpoint else None
        return JsonResponse(_serialize_agent_email_settings(request, agent, endpoint, account))

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        action = str(_email_settings_payload_value(payload, "action", "action", "") or "").strip().lower()
        integration = get_or_create_agent_email_integration(agent)
        if action in {"disconnect_legacy_oauth", "disconnectlegacyoauth"}:
            credential = oauth_credential_for_account(integration.oauth_account)
            if credential is None or canonical_email_provider(credential.provider) in {"gmail", "outlook"}:
                return JsonResponse({"error": "No legacy email OAuth connection is active."}, status=400)
            disconnected = disconnect_agent_email_oauth(agent, credential.provider)
            return JsonResponse({"ok": disconnected, "settings": _serialize_agent_email_settings(request, agent, None, integration.custom_account)})
        if action in {"enable_custom", "enablecustom"}:
            if integration.active_mode == AgentEmailIntegration.ActiveMode.OAUTH:
                return JsonResponse({"error": "Disconnect the connected email provider before enabling custom SMTP/IMAP."}, status=400)
            integration.active_mode = AgentEmailIntegration.ActiveMode.CUSTOM
            integration.save(update_fields=["active_mode", "updated_at"])
            return JsonResponse({"ok": True, "settings": _serialize_agent_email_settings(request, agent, None, integration.custom_account)})
        if action in {"disable_custom", "disablecustom", "reset_to_default", "resettodefault"}:
            if integration.custom_account:
                AgentEmailAccount.objects.filter(pk=integration.custom_account_id).update(
                    is_outbound_enabled=False,
                    is_inbound_enabled=False,
                )
            integration.active_mode = AgentEmailIntegration.ActiveMode.NONE
            integration.save(update_fields=["active_mode", "updated_at"])
            default_endpoint = ensure_default_agent_email_endpoint(agent, is_primary=True)
            return JsonResponse({"ok": True, "settings": _serialize_agent_email_settings(request, agent, default_endpoint, integration.custom_account)})
        if action:
            return JsonResponse({"error": "Unknown email settings action."}, status=400)
        return _save_agent_email_settings(request, agent, integration, payload)


class AgentEmailSettingsTestAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        test_outbound = _coerce_bool(_email_settings_payload_value(payload, "testOutbound", "test_outbound", False))
        test_inbound = _coerce_bool(_email_settings_payload_value(payload, "testInbound", "test_inbound", False))
        if not test_outbound and not test_inbound:
            return JsonResponse({"error": "Select at least one connection test to run."}, status=400)

        integration = (
            AgentEmailIntegration.objects
            .select_related("custom_account__endpoint")
            .filter(agent=agent)
            .first()
        )
        if integration is None or integration.active_mode != AgentEmailIntegration.ActiveMode.CUSTOM:
            return JsonResponse({"error": "Enable custom SMTP/IMAP before testing these settings."}, status=400)
        try:
            expected_mode = _expected_email_mode(payload)
            if expected_mode != integration.active_mode:
                return _email_mode_changed_response()
            test_payload = {
                **payload,
                "isOutboundEnabled": test_outbound,
                "isInboundEnabled": test_inbound,
            }
            _endpoint, test_account, saved_account, _cleaned_data = _build_custom_email_draft(
                agent,
                integration,
                test_payload,
            )
            results, validation_errors = _validate_custom_email_draft(
                test_account,
                saved_account,
                test_payload,
                force_outbound=test_outbound,
                force_inbound=test_inbound,
            )
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)

        return JsonResponse(
            {
                "ok": not validation_errors,
                "results": results,
            }
        )
