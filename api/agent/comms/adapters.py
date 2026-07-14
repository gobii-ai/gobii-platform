"""Adapters for inbound communication providers.

These adapters translate provider-specific webhook payloads into a common
:class:`ParsedMessage` structure used by the rest of the application.
"""

from __future__ import annotations

import json

from django.http.request import QueryDict
from opentelemetry import trace
from dataclasses import dataclass
from typing import Any, List, MutableMapping, Optional
from django.http import HttpRequest
from api.models import CommsChannel
import  logging
import re

from config.settings import EMAIL_STRIP_REPLIES
from api.agent.comms.chat_email_display_cache import merge_chat_body_html_cache
from api.agent.comms.email_forwarding import (
    extract_forward_sections as _extract_forward_sections,
    is_forward_like as _is_forward_like,
)
from api.services.system_settings import get_max_file_size

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')

EMAIL_BODY_HTML_PAYLOAD_KEY = "body_html"


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
    conversation_address: Optional[str] = None


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


def _coerce_email_headers_map(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if k and v is not None}

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return _coerce_email_headers_map(parsed)

    if isinstance(value, (list, tuple)):
        headers: dict[str, str] = {}
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                key = str(item[0]).strip()
                header_value = str(item[1]).strip()
                if key and header_value:
                    headers[key] = header_value
            elif isinstance(item, dict):
                key = str(item.get("Name") or item.get("name") or "").strip()
                header_value = str(item.get("Value") or item.get("value") or "").strip()
                if key and header_value:
                    headers[key] = header_value
        return headers

    return {}


def _normalize_inbound_email_raw_payload(raw_payload: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    payload = dict(raw_payload)

    headers = _coerce_email_headers_map(
        payload.get("headers")
        or payload.get("Headers")
        or payload.get("message-headers")
    )
    if headers:
        payload["headers"] = headers

    message_id = (
        payload.get("message_id")
        or payload.get("MessageID")
        or payload.get("Message-Id")
        or payload.get("Message-ID")
        or headers.get("MessageID")
        or headers.get("Message-Id")
        or headers.get("Message-ID")
        or headers.get("message-id")
    )
    if message_id:
        payload["message_id"] = str(message_id).strip()

    return payload

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
                    attachments.append({
                        "url": media_url,
                        "content_type": data.get(f"MediaContentType{i}", ""),
                    })

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
        max_bytes = get_max_file_size()
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

        normalized_payload = _normalize_inbound_email_raw_payload(payload_dict)
        if body or html_body:
            normalized_payload = merge_chat_body_html_cache(
                normalized_payload,
                body,
                explicit_html=html_body,
            )

        return ParsedMessage(
            sender=payload_dict.get("From", ""),
            recipient=payload_dict.get("To", ""),
            subject=payload_dict.get("Subject"),
            body=body,
            attachments=attachments,
            raw_payload=normalized_payload,
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

        # Get the full unstripped body for forward detection and extraction
        # Mailgun's stripped-text removes quoted content, which we need for forwards
        body_plain_raw = _first_value(payload_dict.get("body-plain")) or ""
        stripped_html_body = _first_value(payload_dict.get("stripped-html")) or ""
        full_html_body = (
            _first_value(payload_dict.get("body-html"))
            or _first_value(payload_dict.get("html"))
            or ""
        )
        html_body = stripped_html_body or full_html_body

        # For non-forwards, prefer stripped content; for forwards, we'll use body-plain
        stripped_text = _first_value(payload_dict.get("stripped-text")) or ""
        working_text = stripped_text or body_plain_raw or _html_to_text(html_body)

        body_used = (
            "stripped-text"
            if stripped_text
            else "body-plain"
            if body_plain_raw
            else "html"
            if html_body
            else "None"
        )

        body = working_text

        # Build attachment metadata for forward detection (filter oversized files)
        max_file_size = get_max_file_size()
        attachments_meta = []
        for att in attachments:
            content_type = getattr(att, "content_type", "")
            file_size = getattr(att, "size", 0)
            if max_file_size and file_size > max_file_size:
                logger.warning(f"Attachment {att.name} is too large to process. Skipping.");
                span.add_event(f"Attachment {att.name} is too large to process. Skipping. Size in bytes: {file_size}")
                continue
            elif content_type:
                attachments_meta.append({"ContentType": content_type})

        # Forward detection and handling.
        # We ALWAYS check for forwards regardless of EMAIL_STRIP_REPLIES, because:
        # 1. Forwards need body-plain to preserve the quoted/forwarded content
        #    (Mailgun's stripped-text removes it)
        # 2. In production, EMAIL_STRIP_REPLIES is False, but working_text still
        #    prefers stripped-text for historical reasons. We preserve that behavior
        #    for non-forwards to avoid breaking existing functionality, but forwards
        #    must use body-plain or the forwarded content is lost.
        detection_text = body_plain_raw or working_text
        is_forward = _is_forward_like(subject, detection_text, attachments_meta)
        span.set_attribute("mailgun.is_forward", bool(is_forward))

        if is_forward:
            # For forwards, use body-plain to preserve the quoted/forwarded content
            forward_text = body_plain_raw or _html_to_text(full_html_body or html_body) or working_text
            preamble, forwarded = _extract_forward_sections(forward_text)
            if forwarded and preamble:
                body = f"{preamble}\n\n{forwarded}"
                body_used = "Forward+Preamble+Block (body-plain)"
            elif forwarded:
                body = forwarded
                body_used = "Forward+BlockOnly (body-plain)"
            elif preamble:
                body = preamble
                body_used = "Forward+PreambleOnly (body-plain)"
            else:
                body = forward_text.strip()
                body_used = "Forward+FullBodyFallback (body-plain)"
        elif EMAIL_STRIP_REPLIES is True:
            span.set_attribute("mailgun.strip_replies", "True")
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
        # else: non-forward with EMAIL_STRIP_REPLIES=False, body stays as working_text

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

        chat_html_body = full_html_body if is_forward and full_html_body else html_body
        normalized_payload = _normalize_inbound_email_raw_payload(payload_dict)
        if body or chat_html_body:
            normalized_payload = merge_chat_body_html_cache(
                normalized_payload,
                body,
                explicit_html=chat_html_body,
            )

        return ParsedMessage(
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
            attachments=attachments,
            raw_payload=normalized_payload,
            msg_channel=CommsChannel.EMAIL,
        )
