from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Sequence
from opentelemetry import trace
import logging

from django.conf import settings

from api.models import AgentEmailAccount
from api.agent.comms.email_oauth import build_xoauth2_string, get_email_oauth_credential, resolve_oauth_identity

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)


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

        # Build message
        msg = EmailMessage()
        msg["Subject"] = subject or ""
        msg["From"] = from_addr
        msg["To"] = ", ".join(list(to_addrs or [])[:1]) if to_addrs else ""
        # If there are more than 1 recipients, put the rest in Cc
        if to_addrs and len(to_addrs) > 1:
            msg["Cc"] = ", ".join(list(to_addrs)[1:])
        msg["Message-ID"] = make_msgid()
        msg["X-Gobii-Message-ID"] = str(attempt_id)

        # Text and HTML alternatives
        msg.set_content(plaintext_body or "")
        if html_body:
            msg.add_alternative(html_body, subtype="html")

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
                credential = get_email_oauth_credential(account)
                if not credential or not credential.access_token:
                    raise RuntimeError("OAuth access token missing for SMTP account")
                identity = resolve_oauth_identity(account, "smtp")
                auth_string = build_xoauth2_string(identity, credential.access_token)
                client.auth("XOAUTH2", lambda _: auth_string)
            elif account.smtp_auth != AgentEmailAccount.AuthMode.NONE:
                client.login(account.smtp_username or "", account.get_smtp_password() or "")

            # Envelope sender should match From/header address typically
            all_rcpts = list(to_addrs or [])
            client.send_message(msg, from_addr=from_addr, to_addrs=all_rcpts)
            return ""
        finally:
            try:
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass
