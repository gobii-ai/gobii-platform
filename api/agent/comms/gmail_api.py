import base64
from typing import Any, Sequence

import requests
from opentelemetry import trace

from api.agent.comms.email_oauth import get_email_oauth_credential
from api.agent.comms.smtp_transport import EmailAttachmentPayload, build_email_message
from api.models import AgentEmailAccount, AgentEmailOAuthCredential


GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_LEGACY_MAIL_SCOPE = "https://mail.google.com/"
GMAIL_API_TIMEOUT_SECONDS = 30
GMAIL_HISTORY_ID_METADATA_KEY = "gmail_history_id"

tracer = trace.get_tracer("gobii.utils")


class GmailApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _scope_set(scope: str) -> set[str]:
    return {item for item in str(scope or "").replace(",", " ").split() if item}


def _stored_credential(account: AgentEmailAccount) -> AgentEmailOAuthCredential | None:
    try:
        return account.oauth_credential
    except AgentEmailOAuthCredential.DoesNotExist:
        return None


def uses_gmail_api(account: AgentEmailAccount) -> bool:
    """Use the Gmail API only for newly scoped credentials.

    Legacy credentials carrying the full mail scope intentionally stay on the
    existing SMTP/IMAP path so deployed agents do not need to reconnect.
    """
    credential = _stored_credential(account)
    if credential is None or str(credential.provider or "").lower() not in {"gmail", "google"}:
        return False
    scopes = _scope_set(credential.scope)
    return GMAIL_LEGACY_MAIL_SCOPE not in scopes and bool(
        scopes.intersection({GMAIL_SEND_SCOPE, GMAIL_READONLY_SCOPE})
    )


def _require_scope(account: AgentEmailAccount, required_scope: str) -> None:
    credential = _stored_credential(account)
    scopes = _scope_set(credential.scope if credential else "")
    if required_scope not in scopes:
        raise GmailApiError(f"Gmail did not grant the required scope: {required_scope}")


def _request(
    account: AgentEmailAccount,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    credential = get_email_oauth_credential(account)
    if credential is None or not credential.access_token:
        raise GmailApiError("Gmail OAuth access token is missing.")

    try:
        response = requests.request(
            method,
            f"{GMAIL_API_ROOT}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {credential.access_token}"},
            params=params,
            json=json,
            timeout=GMAIL_API_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        raise GmailApiError(f"Gmail API request failed: {exc}") from exc

    if not response.ok:
        detail = str(response.text or "").strip()
        if len(detail) > 500:
            detail = f"{detail[:500]}..."
        message = f"Gmail API returned HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise GmailApiError(message, status_code=response.status_code)

    try:
        payload = response.json()
    except ValueError as exc:
        raise GmailApiError("Gmail API returned an invalid JSON response.") from exc
    if not isinstance(payload, dict):
        raise GmailApiError("Gmail API returned an unexpected response.")
    return payload


def get_gmail_profile(account: AgentEmailAccount) -> dict[str, Any]:
    _require_scope(account, GMAIL_READONLY_SCOPE)
    return _request(account, "GET", "profile")


def get_gmail_history_id(account: AgentEmailAccount) -> str:
    credential = _stored_credential(account)
    metadata = credential.metadata if credential and isinstance(credential.metadata, dict) else {}
    return str(metadata.get(GMAIL_HISTORY_ID_METADATA_KEY) or "").strip()


def set_gmail_history_id(account: AgentEmailAccount, history_id: object) -> str:
    normalized = str(history_id or "").strip()
    if not normalized:
        raise GmailApiError("Gmail did not return a mailbox history identifier.")
    credential = get_email_oauth_credential(account)
    if credential is None:
        raise GmailApiError("Gmail OAuth credential is missing.")
    metadata = dict(credential.metadata) if isinstance(credential.metadata, dict) else {}
    metadata[GMAIL_HISTORY_ID_METADATA_KEY] = normalized
    credential.metadata = metadata
    credential.save(update_fields=["metadata", "updated_at"])
    return normalized


def ensure_gmail_history_checkpoint(account: AgentEmailAccount) -> str:
    existing = get_gmail_history_id(account)
    if existing:
        get_gmail_profile(account)
        return existing
    profile = get_gmail_profile(account)
    return set_gmail_history_id(account, profile.get("historyId"))


def reset_gmail_history_checkpoint(account: AgentEmailAccount) -> str:
    profile = get_gmail_profile(account)
    return set_gmail_history_id(account, profile.get("historyId"))


def validate_gmail_send_access(account: AgentEmailAccount) -> None:
    _require_scope(account, GMAIL_SEND_SCOPE)
    # Profile is a non-mutating authenticated request that also verifies the
    # paired readonly permission without sending a test message to the user.
    get_gmail_profile(account)


def validate_gmail_receive_access(account: AgentEmailAccount) -> None:
    _require_scope(account, GMAIL_READONLY_SCOPE)
    ensure_gmail_history_checkpoint(account)


def list_gmail_history(
    account: AgentEmailAccount,
    *,
    start_history_id: str,
    page_token: str = "",
) -> dict[str, Any]:
    _require_scope(account, GMAIL_READONLY_SCOPE)
    params = {
        "startHistoryId": start_history_id,
        "labelId": "INBOX",
        "historyTypes": "messageAdded",
        "maxResults": 500,
    }
    if page_token:
        params["pageToken"] = page_token
    return _request(account, "GET", "history", params=params)


def get_gmail_raw_message(account: AgentEmailAccount, message_id: str) -> bytes:
    _require_scope(account, GMAIL_READONLY_SCOPE)
    payload = _request(
        account,
        "GET",
        f"messages/{message_id}",
        params={"format": "raw"},
    )
    encoded = str(payload.get("raw") or "").strip()
    if not encoded:
        raise GmailApiError(f"Gmail message {message_id} did not include raw content.")
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise GmailApiError(f"Gmail message {message_id} contained invalid raw content.") from exc


class GmailApiTransport:
    @classmethod
    @tracer.start_as_current_span("email.gmail_api.send")
    def send(
        cls,
        account: AgentEmailAccount,
        from_addr: str,
        to_addrs: Sequence[str],
        subject: str,
        plaintext_body: str,
        html_body: str,
        attempt_id: str,
        message_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        attachments: Sequence[EmailAttachmentPayload] | None = None,
        envelope_from_addr: str | None = None,
    ) -> str:
        del envelope_from_addr
        _require_scope(account, GMAIL_SEND_SCOPE)
        msg = build_email_message(
            from_addr=from_addr,
            to_addrs=to_addrs,
            subject=subject,
            plaintext_body=plaintext_body,
            html_body=html_body,
            attempt_id=attempt_id,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
        )
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        payload = _request(account, "POST", "messages/send", json={"raw": raw})
        return str(payload.get("id") or "")
