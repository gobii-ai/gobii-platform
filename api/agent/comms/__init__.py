"""Communication helpers for persistent agents.

This package contains the building blocks for communicating with persistent
agents.  Adapters normalize inbound webhook payloads while sender classes
provide an abstraction over outbound providers.
"""

from .adapters import (
    SmsAdapter,
    EmailAdapter,
    TwilioSmsAdapter,
    PostmarkEmailAdapter,
    MailgunEmailAdapter,
    ParsedMessage,
)
from .senders import SmsSender, EmailSender
from .message_service import ingest_inbound_message, InboundMessageInfo

__all__ = [
    "ParsedMessage",
    "SmsAdapter",
    "EmailAdapter",
    "TwilioSmsAdapter",
    "PostmarkEmailAdapter",
    "MailgunEmailAdapter",
    "SmsSender",
    "EmailSender",
    "ingest_inbound_message",
    "InboundMessageInfo",
]
