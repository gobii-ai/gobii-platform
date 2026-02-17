import ast
import logging
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views import View

from api.encryption import SecretsEncryption
from api.models import (
    AgentEmailAccount,
    AgentEmailOAuthCredential,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
)
from console.api_views import (
    ApiLoginRequiredMixin,
    _coerce_bool,
    _parse_json_body,
)
from console.forms import AgentEmailAccountConsoleForm
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


logger = logging.getLogger(__name__)

EMAIL_OAUTH_PROVIDER_DEFAULTS = {
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_security": "ssl",
    },
}
EMAIL_ENDPOINT_REQUIRED_ERROR = "Please provide a valid email address."
EMAIL_ENDPOINT_CONFLICT_ERROR = "That email address is already assigned to another agent."
AGENT_EMAIL_ACCOUNT_COPY_EXCLUDED_FIELDS = {"endpoint", "created_at", "updated_at"}
AGENT_EMAIL_ACCOUNT_PASSWORD_INPUT_FIELDS = {"smtp_password", "imap_password"}
AGENT_EMAIL_ACCOUNT_COPY_FIELDS = tuple(
    field.name
    for field in AgentEmailAccount._meta.concrete_fields
    if field.name not in AGENT_EMAIL_ACCOUNT_COPY_EXCLUDED_FIELDS
)


def _resolve_owned_agent_for_email_settings(request: HttpRequest, agent_id: str) -> PersistentAgent:
    return get_object_or_404(
        PersistentAgent.objects.non_eval().alive(),
        pk=agent_id,
        user=request.user,
    )


def _get_agent_email_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint | None:
    endpoint = agent.comms_endpoints.filter(
        channel=CommsChannel.EMAIL,
        owner_agent=agent,
        is_primary=True,
    ).first()
    if endpoint:
        return endpoint
    return agent.comms_endpoints.filter(
        channel=CommsChannel.EMAIL,
        owner_agent=agent,
    ).first()


def _copy_agent_email_account_data(source: AgentEmailAccount, target: AgentEmailAccount) -> None:
    for field in AGENT_EMAIL_ACCOUNT_COPY_FIELDS:
        setattr(target, field, getattr(source, field))


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

    _save_agent_email_endpoint_updates(current_endpoint, agent, normalized_address)
    return current_endpoint


def _move_agent_email_account_data(source: AgentEmailAccount, target: AgentEmailAccount) -> None:
    _copy_agent_email_account_data(source, target)
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

    current_endpoint = _get_agent_email_endpoint(agent)
    existing_account = getattr(current_endpoint, "agentemailaccount", None) if current_endpoint else None

    with transaction.atomic():
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
        if not account.smtp_username:
            account.smtp_username = endpoint.address
        if not account.imap_username:
            account.imap_username = endpoint.address

        provider_key = (provider or "").lower()
        if not provider_key:
            try:
                provider_key = (account.oauth_credential.provider or "").lower()
            except AgentEmailOAuthCredential.DoesNotExist:
                provider_key = ""
        defaults = EMAIL_OAUTH_PROVIDER_DEFAULTS.get(provider_key)
        if defaults:
            for key, value in defaults.items():
                if not getattr(account, key):
                    setattr(account, key, value)


def _validate_agent_smtp_connection(account: AgentEmailAccount) -> tuple[bool, str]:
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
        return False, _format_email_connection_error(exc)


def _validate_agent_imap_connection(account: AgentEmailAccount) -> tuple[bool, str]:
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
        return False, _format_email_connection_error(exc)


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


def _format_email_connection_error(raw_error: Any) -> str:
    normalized = _normalize_email_error_text(raw_error)
    lowered = normalized.lower()
    if "empty username or password" in lowered:
        return "Username or password is missing. Enter both values and try again."
    if (
        "username and password not accepted" in lowered
        or "badcredentials" in lowered
        or "authentication failed" in lowered
        or "invalid credentials" in lowered
    ):
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


def _serialize_agent_email_settings(
    request: HttpRequest,
    agent: PersistentAgent,
    endpoint: PersistentAgentCommsEndpoint | None,
    account: AgentEmailAccount | None,
) -> dict[str, Any]:
    credential = None
    if account:
        try:
            credential = account.oauth_credential
        except AgentEmailOAuthCredential.DoesNotExist:
            credential = None

    is_first_time_custom_setup = _is_first_time_custom_email_setup(account, credential)

    imap_idle_enabled = True
    if account and not is_first_time_custom_setup:
        imap_idle_enabled = bool(account.imap_idle_enabled)

    endpoint_payload = {
        "address": endpoint.address if endpoint else "",
        "exists": endpoint is not None,
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
    }

    oauth_payload = {
        "connected": credential is not None,
        "provider": credential.provider if credential else "",
        "scope": credential.scope if credential else "",
        "expiresAt": credential.expires_at.isoformat() if credential and credential.expires_at else None,
        "callbackPath": reverse("console-email-oauth-callback-view"),
        "startUrl": reverse("console-email-oauth-start"),
        "statusUrl": reverse("console-email-oauth-status", args=[account.pk]) if account else None,
        "revokeUrl": reverse("console-email-oauth-revoke", args=[account.pk]) if account else None,
    }

    return {
        "agent": {
            "id": str(agent.pk),
            "name": agent.name,
            "backUrl": reverse("agent_detail", args=[agent.pk]),
            "helpUrl": "https://docs.gobii.ai/advanced-usage/custom-email-settings",
        },
        "providerDefaults": EMAIL_OAUTH_PROVIDER_DEFAULTS,
        "endpoint": endpoint_payload,
        "account": account_payload,
        "oauth": oauth_payload,
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


class AgentEmailSettingsAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        endpoint = _get_agent_email_endpoint(agent)
        account = getattr(endpoint, "agentemailaccount", None) if endpoint else None
        return JsonResponse(_serialize_agent_email_settings(request, agent, endpoint, account))

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_address = (_email_settings_payload_value(payload, "endpointAddress", "endpoint_address", "") or "").strip()
        if not endpoint_address:
            existing_endpoint = _get_agent_email_endpoint(agent)
            endpoint_address = existing_endpoint.address if existing_endpoint else ""
        if not endpoint_address:
            return JsonResponse({"error": "Agent email address is required."}, status=400)

        try:
            endpoint, account, created = _ensure_agent_email_endpoint_and_account(agent, endpoint_address)
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)

        form_input = _build_email_settings_form_input(payload)
        form = AgentEmailAccountConsoleForm(form_input)
        if not form.is_valid():
            return JsonResponse({"errors": _build_email_form_error_payload(form)}, status=400)

        provider = str(_email_settings_payload_value(payload, "oauthProvider", "oauth_provider", "") or "").strip().lower()
        _apply_email_account_settings(
            account,
            endpoint,
            form.cleaned_data,
            provider=provider,
        )

        try:
            account.full_clean()
            account.save()
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)

        try:
            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.EMAIL_ACCOUNT_CREATED if created else AnalyticsEvent.EMAIL_ACCOUNT_UPDATED,
                source=AnalyticsSource.WEB,
                properties={"agent_id": str(agent.pk), "endpoint": endpoint.address},
            )
        except Exception:
            pass

        return JsonResponse(
            {
                "ok": True,
                "settings": _serialize_agent_email_settings(request, agent, endpoint, account),
            }
        )


class AgentEmailSettingsEnsureAccountAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_address = (_email_settings_payload_value(payload, "endpointAddress", "endpoint_address", "") or "").strip()
        if not endpoint_address:
            return JsonResponse({"error": "Agent email address is required."}, status=400)

        try:
            endpoint, account, _created = _ensure_agent_email_endpoint_and_account(agent, endpoint_address)
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)

        return JsonResponse(
            {
                "ok": True,
                "settings": _serialize_agent_email_settings(request, agent, endpoint, account),
            }
        )


class AgentEmailSettingsTestAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, agent_id: str, *args: Any, **kwargs: Any):
        agent = _resolve_owned_agent_for_email_settings(request, agent_id)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        endpoint_address = (_email_settings_payload_value(payload, "endpointAddress", "endpoint_address", "") or "").strip()
        if not endpoint_address:
            existing_endpoint = _get_agent_email_endpoint(agent)
            endpoint_address = existing_endpoint.address if existing_endpoint else ""
        if not endpoint_address:
            return JsonResponse({"error": "Agent email address is required."}, status=400)

        test_outbound = _coerce_bool(_email_settings_payload_value(payload, "testOutbound", "test_outbound", False))
        test_inbound = _coerce_bool(_email_settings_payload_value(payload, "testInbound", "test_inbound", False))
        if not test_outbound and not test_inbound:
            return JsonResponse({"error": "Select at least one connection test to run."}, status=400)

        try:
            endpoint, account, _created = _ensure_agent_email_endpoint_and_account(agent, endpoint_address)
        except ValidationError as exc:
            return JsonResponse({"errors": exc.message_dict}, status=400)

        form_input = _build_email_settings_form_input(payload)
        form_input["is_outbound_enabled"] = test_outbound
        form_input["is_inbound_enabled"] = test_inbound
        form = AgentEmailAccountConsoleForm(form_input)
        if not form.is_valid():
            return JsonResponse({"errors": _build_email_form_error_payload(form)}, status=400)

        provider = str(_email_settings_payload_value(payload, "oauthProvider", "oauth_provider", "") or "").strip().lower()
        test_account = AgentEmailAccount.objects.get(pk=account.pk)
        _apply_email_account_settings(test_account, endpoint, form.cleaned_data, provider=provider)

        smtp_result: dict[str, Any] | None = None
        imap_result: dict[str, Any] | None = None
        errors: list[str] = []

        if test_outbound:
            smtp_ok, smtp_error = _validate_agent_smtp_connection(test_account)
            smtp_result = {"ok": smtp_ok, "error": smtp_error}
            if not smtp_ok:
                errors.append(f"SMTP test failed: {smtp_error}")

        if test_inbound:
            imap_ok, imap_error = _validate_agent_imap_connection(test_account)
            imap_result = {"ok": imap_ok, "error": imap_error}
            if not imap_ok:
                errors.append(f"IMAP test failed: {imap_error}")

        if (smtp_result and smtp_result["ok"]) or (imap_result and imap_result["ok"]):
            account.connection_last_ok_at = timezone.now()
        account.connection_error = "; ".join(errors)
        account.save(update_fields=["connection_last_ok_at", "connection_error", "updated_at"])

        return JsonResponse(
            {
                "ok": not errors,
                "results": {
                    "smtp": smtp_result,
                    "imap": imap_result,
                },
                "settings": _serialize_agent_email_settings(request, agent, endpoint, account),
            }
        )
