import ast
import logging
import re
from typing import Any

from api.models import AgentEmailAccount, AgentEmailOAuthCredential


logger = logging.getLogger(__name__)


def validate_agent_smtp_connection(account: AgentEmailAccount) -> tuple[bool, str]:
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
        provider = _email_oauth_provider(account)
        logger.warning(
            "SMTP connection test failed for agent email account %s endpoint %s provider %s auth %s: %r",
            account.pk,
            account.endpoint_id,
            provider,
            account.smtp_auth,
            exc,
            exc_info=exc,
        )
        return False, format_email_connection_error(
            exc,
            channel="smtp",
            auth_mode=account.smtp_auth,
            provider=provider,
        )


def validate_agent_imap_connection(account: AgentEmailAccount) -> tuple[bool, str]:
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
        provider = _email_oauth_provider(account)
        logger.warning(
            "IMAP connection test failed for agent email account %s endpoint %s provider %s auth %s: %r",
            account.pk,
            account.endpoint_id,
            provider,
            account.imap_auth,
            exc,
            exc_info=exc,
        )
        return False, format_email_connection_error(
            exc,
            channel="imap",
            auth_mode=account.imap_auth,
            provider=provider,
        )


def _decode_email_error_part(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="ignore").strip()
    return str(value).strip()


def _normalize_email_error_text(raw_error: Any) -> str:
    text = str(raw_error or "").strip()
    if not text:
        return ""

    if text.startswith("(") and text.endswith(")"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, tuple):
            flattened = " ".join(_decode_email_error_part(part) for part in parsed if part is not None).strip()
            if flattened:
                text = flattened

    if (text.startswith("b'") and text.endswith("'")) or (text.startswith('b"') and text.endswith('"')):
        try:
            parsed_bytes = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed_bytes = None
        if isinstance(parsed_bytes, (bytes, bytearray)):
            text = parsed_bytes.decode("utf-8", errors="ignore").strip()

    text = text.replace("\\r", " ").replace("\\n", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip(" '\"")


def _email_oauth_provider(account: AgentEmailAccount) -> str:
    try:
        return (account.oauth_credential.provider or "").strip().lower()
    except AgentEmailOAuthCredential.DoesNotExist:
        return ""


def format_email_connection_error(
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
