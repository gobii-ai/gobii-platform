from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Sequence
from opentelemetry import trace
import logging

from django.conf import settings

from api.models import AgentEmailAccount
from api.agent.comms.email_oauth import build_xoauth2_string, resolve_oauth_identity_and_token

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailAttachmentPayload:
    filename: str
    content: bytes
    content_type: str
    content_id: str | None = None
    disposition: str = "attachment"

    @property
    def is_inline(self) -> bool:
        return self.disposition == "inline" and bool(self.content_id)


def _to_mime_type_parts(content_type: str) -> tuple[str, str]:
    base_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if "/" not in base_content_type:
        return "application", "octet-stream"
    maintype, subtype = base_content_type.split("/", 1)
    return maintype or "application", subtype or "octet-stream"


def _attach_payloads(msg: EmailMessage, attachments: Sequence[EmailAttachmentPayload]) -> None:
    for attachment in attachments:
        maintype, subtype = _to_mime_type_parts(attachment.content_type)
        if attachment.is_inline:
            html_part = msg.get_body(preferencelist=("html",))
            if html_part is not None:
                html_part.add_related(
                    attachment.content,
                    maintype=maintype,
                    subtype=subtype,
                    cid=f"<{attachment.content_id}>",
                    filename=attachment.filename,
                    disposition="inline",
                )
                continue

        msg.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )


class SmtpTransport:
    """Simple SMTP transport for per-agent SMTP accounts.

    One connection per send; no pooling.
    """

    DEFAULT_TIMEOUT = 30

    @classmethod
    @tracer.start_as_current_span("email.smtp.send")
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
    ) -> str:
        """Send email using the provided account.

        Returns a provider message id if available, else empty string.
        """
        span = trace.get_current_span()
        span.set_attribute("smtp.host", account.smtp_host)
        span.set_attribute("smtp.port", int(account.smtp_port or 0))
        span.set_attribute("smtp.security", account.smtp_security)
        span.set_attribute("smtp.auth", account.smtp_auth)
        # Attribute names aligned with plan for quick filtering
        to_count = 1 if (to_addrs and len(list(to_addrs)) >= 1) else 0
        cc_count = max(0, (len(list(to_addrs or [])) - 1))
        span.set_attribute("to_count", to_count)
        span.set_attribute("cc_count", cc_count)

        recipient_list = list(to_addrs or [])

        # Build message
        msg = EmailMessage()
        msg["Subject"] = subject or ""
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipient_list[:1]) if recipient_list else ""
        # If there are more than 1 recipients, put the rest in Cc
        if len(recipient_list) > 1:
            msg["Cc"] = ", ".join(recipient_list[1:])
        msg["Message-ID"] = message_id or make_msgid()
        msg["X-Gobii-Message-ID"] = str(attempt_id)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        # Text and HTML alternatives
        msg.set_content(plaintext_body or "")
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        if attachments:
            _attach_payloads(msg, attachments)

        # Connect and send
        if account.smtp_security == AgentEmailAccount.SmtpSecurity.SSL:
            client: smtplib.SMTP | smtplib.SMTP_SSL = smtplib.SMTP_SSL(
                account.smtp_host, int(account.smtp_port or 465), timeout=cls.DEFAULT_TIMEOUT
            )
        else:
            client = smtplib.SMTP(
                account.smtp_host, int(account.smtp_port or 587), timeout=cls.DEFAULT_TIMEOUT
            )
        try:
            client.ehlo()
            if account.smtp_security == AgentEmailAccount.SmtpSecurity.STARTTLS:
                client.starttls()
                client.ehlo()

            # Auth
            if account.smtp_auth == AgentEmailAccount.AuthMode.OAUTH2:
                identity, access_token, _credential = resolve_oauth_identity_and_token(account, "smtp")
                auth_string = build_xoauth2_string(identity, access_token)
                client.auth("XOAUTH2", lambda _=None: auth_string)
            elif account.smtp_auth != AgentEmailAccount.AuthMode.NONE:
                client.login(account.smtp_username or "", account.get_smtp_password() or "")

            # Envelope sender should match From/header address typically
            client.send_message(msg, from_addr=from_addr, to_addrs=recipient_list)
            return ""
        finally:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass
