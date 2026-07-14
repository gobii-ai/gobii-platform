import smtplib
from dataclasses import dataclass
from email.message import EmailMessage, MIMEPart
from email.utils import make_msgid, parseaddr
from typing import Any, Sequence
from opentelemetry import trace
import logging


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
    source_node: Any | None = None
    source_path: str | None = None
    size_bytes: int | None = None

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
            related_part = _find_related_part(msg)
            if related_part is not None:
                _attach_inline_payload_to_related(related_part, attachment, maintype, subtype)
                continue

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


def _find_related_part(msg: EmailMessage) -> EmailMessage | None:
    for part in msg.walk():
        if part.get_content_type() == "multipart/related":
            return part
    return None


def _attach_inline_payload_to_related(
    related_part: EmailMessage,
    attachment: EmailAttachmentPayload,
    maintype: str,
    subtype: str,
) -> None:
    inline_part = MIMEPart()
    inline_part.set_content(
        attachment.content,
        maintype=maintype,
        subtype=subtype,
        disposition="inline",
        filename=attachment.filename,
    )
    inline_part["Content-ID"] = f"<{attachment.content_id}>"
    related_part.attach(inline_part)


def build_email_message(
    *,
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
) -> EmailMessage:
    """Build the RFC 2822 message shared by SMTP and provider API transports."""
    recipient_list = list(to_addrs or [])
    msg = EmailMessage()
    msg["Subject"] = subject or ""
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipient_list[:1]) if recipient_list else ""
    if len(recipient_list) > 1:
        msg["Cc"] = ", ".join(recipient_list[1:])
    msg["Message-ID"] = message_id or make_msgid()
    msg["X-Gobii-Message-ID"] = str(attempt_id)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    msg.set_content(plaintext_body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    if attachments:
        _attach_payloads(msg, attachments)
    return msg


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
        envelope_from_addr: str | None = None,
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
        envelope_sender = envelope_from_addr or parseaddr(from_addr)[1] or from_addr

        msg = build_email_message(
            from_addr=from_addr,
            to_addrs=recipient_list,
            subject=subject,
            plaintext_body=plaintext_body,
            html_body=html_body,
            attempt_id=attempt_id,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
        )

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
            client.send_message(msg, from_addr=envelope_sender, to_addrs=recipient_list)
            return ""
        finally:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass
