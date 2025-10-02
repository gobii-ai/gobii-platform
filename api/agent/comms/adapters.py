"""Adapters for inbound communication providers.

These adapters translate provider-specific webhook payloads into a common
:class:`ParsedMessage` structure used by the rest of the application.
"""

from __future__ import annotations

import json

from django.http.request import QueryDict
from opentelemetry import trace
from dataclasses import dataclass
from typing import Any, List, MutableMapping, Optional, Tuple
from django.http import HttpRequest
from api.models import CommsChannel
import  logging
import re

from config.settings import EMAIL_STRIP_REPLIES
from config import settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')


FORWARD_MARKERS = [
    r"^Begin forwarded message:",
    r"^-{2,}\s*Forwarded message\s*-{2,}$",
    r"^-----Original Message-----$",
]
HEADER_BLOCK_RE = re.compile(
    r"(?m)^(From:\s*.+)\n(?:.+\n){0,6}?(Date:\s*.+|Sent:\s*.+)\n(?:.+\n){0,6}?(Subject:\s*.+)\n(?:.+\n){0,6}?(To:\s*.+)",
    re.IGNORECASE,
)
FORWARD_MARKERS_RE = re.compile("|".join(FORWARD_MARKERS), re.IGNORECASE | re.MULTILINE)
SUBJECT_FWD_RE = re.compile(r"^\s*(fwd?|fw|wg|tr|rv)\s*:", re.IGNORECASE)


def _is_forward_like(subject: str, body_text: str, attachments: list[dict]) -> bool:
    if any((a.get("ContentType", "") or "").lower() == "message/rfc822" for a in (attachments or [])):
        return True
    if SUBJECT_FWD_RE.search(subject or ""):
        return True
    if FORWARD_MARKERS_RE.search(body_text or ""):
        return True
    if HEADER_BLOCK_RE.search(body_text or ""):
        return True
    return False


def _extract_forward_sections(body_text: str) -> Tuple[str, str]:
    """
    Returns (preamble, forwarded_block). If no marker found, returns (body_text, "").
    """
    if not body_text:
        return "", ""
    starts = []
    m1 = FORWARD_MARKERS_RE.search(body_text)
    if m1:
        starts.append(m1.start())
    m2 = HEADER_BLOCK_RE.search(body_text)
    if m2:
        starts.append(m2.start())
    if not starts:
        return body_text.strip(), ""
    idx = min(starts)
    return body_text[:idx].strip(), body_text[idx:].strip()


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        # strong, layout-aware conversion
        from inscriptis import get_text  # pip install inscriptis
        return get_text(html)
    except Exception:
        # minimal fallback
        return re.sub(r"<[^>]+>", "", html)


@dataclass
class ParsedMessage:
    """Normalized representation of an inbound message."""
    sender: str
    recipient: str
    subject: Optional[str]
    body: str
    attachments: List[Any]
    raw_payload: MutableMapping[str, Any]
    msg_channel: CommsChannel


class SmsAdapter:
    """Base adapter interface for SMS webhooks."""

    def parse_request(self, request: HttpRequest) -> ParsedMessage:  # pragma: no cover - interface
        """Return a :class:`ParsedMessage` extracted from ``request``."""
        raise NotImplementedError


class EmailAdapter:
    """Base adapter interface for email webhooks."""

    def parse_request(self, request: HttpRequest) -> ParsedMessage:  # pragma: no cover - interface
        """Return a :class:`ParsedMessage` extracted from ``request``."""
        raise NotImplementedError

class TwilioSmsAdapter(SmsAdapter):
    """Adapter that normalizes Twilio SMS webhook payloads."""


    @staticmethod
    @tracer.start_as_current_span("TWILIO SMS Parse")
    def parse_request(request: HttpRequest) -> ParsedMessage:
        data = request.POST

        try:
            num_media = int(data.get("NumMedia", 0))
        except (TypeError, ValueError):
            num_media = 0

        attachments: List[str] = []

        with tracer.start_as_current_span("TWILIO SMS Parse Attachments"):
            for i in range(num_media):
                media_url = data.get(f"MediaUrl{i}")
                if media_url:
                    attachments.append(media_url)

        return ParsedMessage(
            sender=data.get("From", ""),
            recipient=data.get("To", ""),
            subject=None,
            body=data.get("Body", ""),
            attachments=attachments,
            raw_payload=data.dict(),
            msg_channel=CommsChannel.SMS,
        )


class PostmarkEmailAdapter(EmailAdapter):
    """Adapter that normalizes Postmark inbound webhook payloads."""

    @tracer.start_as_current_span("POSTMARK Email Parse")
    def parse_request(self, request: HttpRequest) -> ParsedMessage:
        """Parse a Postmark webhook request into a ParsedMessage."""
        span = trace.get_current_span()
        payload_dict: MutableMapping[str, Any]

        if hasattr(request, "data"):  # Likely DRF Request
            payload_dict = request.data
        elif request.body and request.content_type == "application/json":
            try:
                payload_dict = json.loads(request.body)
            except json.JSONDecodeError:
                # Log the error for malformed JSON payloads
                logger.warning("Postmark webhook received malformed JSON: %s", request.body)
                payload_dict = {}
        elif isinstance(request.POST, QueryDict) and request.POST:  # Standard form data
            payload_dict = request.POST.dict()
        else:  # Fallback for other cases, or empty POST/body
            payload_dict = {}

        attachments = payload_dict.get("Attachments") or []

        # Enforce max file size on inbound email attachments if Postmark provided ContentLength
        # (we do not decode content here; just filter metadata-labeled oversize attachments)
        try:
            max_bytes = int(settings.MAX_FILE_SIZE)
        except (ValueError, TypeError):
            max_bytes = 0
        if isinstance(attachments, list) and max_bytes:
            def _within_size(a: Any) -> bool:
                try:
                    content_length = int((a or {}).get("ContentLength", 0))
                    return content_length <= max_bytes if content_length else True
                except (ValueError, TypeError):
                    return True
            filtered = [a for a in attachments if _within_size(a)]
            dropped = len(attachments) - len(filtered)
            if dropped:
                span.set_attribute("postmark.attachments.dropped_oversize", dropped)
            attachments = filtered

        if isinstance(attachments, list):
            span.set_attribute("postmark.attachments.count", len(attachments))

        subject = (payload_dict.get("Subject") or "").strip()
        text_body = (payload_dict.get("TextBody") or "")
        html_body = (payload_dict.get("HtmlBody") or "")

        # Normalize a working plain-text body (for forward detection)
        body = ""
        working_text = text_body or _html_to_text(html_body)
        body_used = "TextBody" if text_body else "HtmlBody" if html_body else "None"


        # Detect forwards
        if EMAIL_STRIP_REPLIES is True:
            span.set_attribute("postmark.strip_replies", "True")
            is_forward = _is_forward_like(subject, working_text, attachments)
            span.set_attribute("postmark.is_forward", bool(is_forward))

            if is_forward:
                preamble, forwarded = _extract_forward_sections(working_text)

                if forwarded and preamble:
                    body = f"{preamble}\n\n{forwarded}"
                    body_used = "Forward+Preamble+Block (Text/HTML)"
                elif forwarded:
                    body = forwarded
                    body_used = "Forward+BlockOnly (Text/HTML)"
                elif preamble:
                    # Very rare: marker logic failed to slice; at least return what user typed
                    body = preamble
                    body_used = "Forward+PreambleOnly (Text/HTML)"
                else:
                    # Last-ditch: don’t lose content
                    body = working_text.strip()
                    body_used = "Forward+WorkingTextFallback"
            else:
                # Postmark can have multiple body fields; prefer stripped text reply if available
                body = payload_dict.get("StrippedTextReply") or payload_dict.get("TextBody") or payload_dict.get("HtmlBody") or ""

                # Mark as an attribute what body was used
                if "StrippedTextReply" in payload_dict:
                    body_used = "StrippedTextReply"
                elif "TextBody" in payload_dict:
                    body_used = "TextBody"
                elif "HtmlBody" in payload_dict:
                    body_used = "HtmlBody"
                else:
                    body_used = "Body Missing"
        else:
            body = working_text

        span.set_attribute("postmark.body_used", body_used)

        return ParsedMessage(
            sender=payload_dict.get("From", ""),
            recipient=payload_dict.get("To", ""),
            subject=payload_dict.get("Subject"),
            body=body,
            attachments=attachments,
            raw_payload=payload_dict,
            msg_channel=CommsChannel.EMAIL,
        )


class MailgunEmailAdapter(EmailAdapter):
    """Adapter that normalizes Mailgun inbound webhook payloads."""

    @tracer.start_as_current_span("MAILGUN Email Parse")
    def parse_request(self, request: HttpRequest) -> ParsedMessage:
        """Parse a Mailgun webhook request into a :class:`ParsedMessage`."""
        span = trace.get_current_span()

        if hasattr(request, "data") and not request.POST:
            post_data = request.data
        else:
            post_data = request.POST

        if isinstance(post_data, QueryDict):
            payload_dict: MutableMapping[str, Any] = {
                key: post_data.getlist(key) if len(post_data.getlist(key)) > 1 else post_data.get(key)
                for key in post_data.keys()
            }
        else:
            payload_dict = dict(post_data or {})  # type: ignore[arg-type]

        attachments: List[Any] = []
        if hasattr(request, "FILES") and request.FILES:
            attachments = list(request.FILES.values())

        span.set_attribute("mailgun.attachments.count", len(attachments))

        def _first_value(value: Any) -> Any:
            if isinstance(value, (list, tuple)):
                return value[0] if value else ""
            return value

        subject = (_first_value(payload_dict.get("subject")) or "").strip()

        text_body = (
            _first_value(payload_dict.get("stripped-text"))
            or _first_value(payload_dict.get("body-plain"))
            or _first_value(payload_dict.get("text"))
            or ""
        )
        html_body = (
            _first_value(payload_dict.get("stripped-html"))
            or _first_value(payload_dict.get("body-html"))
            or _first_value(payload_dict.get("html"))
            or ""
        )

        working_text = text_body or _html_to_text(html_body)
        body_used = (
            "stripped-text"
            if payload_dict.get("stripped-text")
            else "body-plain"
            if payload_dict.get("body-plain")
            else "html"
            if html_body
            else "None"
        )

        body = working_text

        if EMAIL_STRIP_REPLIES is True:
            span.set_attribute("mailgun.strip_replies", "True")
            attachments_meta = []
            for att in attachments:
                content_type = getattr(att, "content_type", "")
                if content_type:
                    attachments_meta.append({"ContentType": content_type})
            is_forward = _is_forward_like(subject, working_text, attachments_meta)
            span.set_attribute("mailgun.is_forward", bool(is_forward))

            if is_forward:
                preamble, forwarded = _extract_forward_sections(working_text)
                if forwarded and preamble:
                    body = f"{preamble}\n\n{forwarded}"
                    body_used = "Forward+Preamble+Block"
                elif forwarded:
                    body = forwarded
                    body_used = "Forward+BlockOnly"
                elif preamble:
                    body = preamble
                    body_used = "Forward+PreambleOnly"
                else:
                    body = working_text.strip()
                    body_used = "Forward+WorkingTextFallback"
            else:
                for field in ("stripped-text", "body-plain", "text"):
                    value = _first_value(payload_dict.get(field))
                    if value:
                        body = value
                        body_used = field
                        break
                else:  # No plain text body found, try HTML
                    for field in ("stripped-html", "body-html", "html"):
                        value = _first_value(payload_dict.get(field))
                        if value:
                            body = _html_to_text(value)
                            body_used = field
                            break
        span.set_attribute("mailgun.body_used", body_used)

        sender = (
            _first_value(payload_dict.get("sender"))
            or _first_value(payload_dict.get("from"))
            or ""
        ).strip()
        recipient = (
            _first_value(payload_dict.get("recipient"))
            or _first_value(payload_dict.get("to"))
            or ""
        ).strip()

        return ParsedMessage(
            sender=sender,
            recipient=recipient,
            subject=_first_value(payload_dict.get("subject")),
            body=body,
            attachments=attachments,
            raw_payload=payload_dict,
            msg_channel=CommsChannel.EMAIL,
        )
