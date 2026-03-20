from __future__ import annotations

"""IMAP adapter to normalize RFC822 emails into ParsedMessage.

This module parses raw RFC822 bytes fetched from an IMAP server and converts
them into the common ParsedMessage structure used by the ingestion pipeline.
"""

import email
from email import policy
from email.header import decode_header, make_header
from email.utils import parseaddr
from dataclasses import dataclass
from typing import Any, MutableMapping, Optional, Tuple, List

from django.core.files.base import ContentFile

from api.models import CommsChannel
from .adapters import (
    ParsedMessage,
    _html_to_text,
    _is_forward_like,
    _extract_forward_sections,
)
from .attachment_filters import is_signature_image_attachment
from .rejected_attachments import build_rejected_attachment_metadata
from config.settings import EMAIL_STRIP_REPLIES
from api.services.system_settings import get_max_file_size
import logging

logger = logging.getLogger(__name__)


def _decode_header_value(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value or ""


def _decode_part_payload(part: email.message.EmailMessage) -> str | None:
    try:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        return None


def _extract_text_parts(msg: email.message.EmailMessage) -> Tuple[str, str, str | None]:
    """Return best-effort plain text body, source note, and preserved HTML when safe.

    Preference order:
    1) text/plain (non-attachment)
    2) text/html → text via _html_to_text
    3) entire message string as last resort
    """
    plain_body: str | None = None
    html_body: str | None = None

    # 1) text/plain
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            dispo = (part.get_content_disposition() or "").lower()
            if ctype == "text/html" and dispo != "attachment" and not html_body:
                html_body = _decode_part_payload(part)
            if ctype == "text/plain" and dispo != "attachment" and plain_body is None:
                text = _decode_part_payload(part)
                if text is not None:
                    plain_body = text
        if plain_body is not None:
            return plain_body, "text/plain", html_body
    else:
        content_type = (msg.get_content_type() or "").lower()
        if content_type == "text/plain":
            text = _decode_part_payload(msg)
            if text is not None:
                return text, "text/plain", None
        elif content_type == "text/html":
            html_body = _decode_part_payload(msg)

    # 2) text/html → text
    if html_body:
        return _html_to_text(html_body), "text/html→text", html_body

    # 3) fallback: raw
    try:
        return msg.get_body(preferencelist=('plain', 'html')).get_content(), "fallback/body", None
    except Exception:
        try:
            return msg.as_string(), "fallback/as_string", None
        except Exception:
            return "", "fallback/empty", None


def _collect_attachments(msg: email.message.EmailMessage) -> Tuple[List[Any], List[dict[str, Any]]]:
    """Collect attachments (including inline) as ContentFile objects.

    Applies MAX_FILE_SIZE filtering best-effort based on decoded bytes length.
    """
    files: List[Any] = []
    rejected_attachments: List[dict[str, Any]] = []
    max_bytes = get_max_file_size()

    for part in msg.walk():
        ctype = (part.get_content_type() or "").lower()
        dispo = (part.get_content_disposition() or "").lower()

        # Collect attachments. Skip common inline images to reduce storage.
        if dispo not in ("attachment", "inline"):
            continue

        try:
            raw = part.get_payload(decode=True)
            if raw is None:
                continue
            # Skip inline images (cid) – strongly recommended default
            if dispo == "inline" and ctype.startswith("image/"):
                continue
            if max_bytes and len(raw) > int(max_bytes):
                logger.warning("IMAP attachment exceeds max size; skipping (size=%d, limit=%d)", len(raw), max_bytes)
                rejected_attachments.append(
                    build_rejected_attachment_metadata(
                        filename=part.get_filename() or "attachment",
                        channel=CommsChannel.EMAIL,
                        limit_bytes=max_bytes,
                        reason_code="too_large",
                        size_bytes=len(raw),
                    )
                )
                continue

            filename = part.get_filename() or "attachment"
            charset = part.get_content_charset() or "utf-8"
            # Normalize filename if it is encoded per RFC
            try:
                filename = str(make_header(decode_header(filename)))
            except Exception:
                pass
            if is_signature_image_attachment(filename, ctype):
                continue

            cf = ContentFile(raw, name=filename)
            # annotate metadata for downstream saver
            setattr(cf, "content_type", ctype)
            # size property exists on ContentFile, but ensure attribute for saver checks
            try:
                setattr(cf, "size", cf.size)
            except Exception:
                setattr(cf, "size", len(raw))

            files.append(cf)
        except Exception:
            logger.debug("Failed to decode attachment part", exc_info=True)
            continue

    return files, rejected_attachments


@dataclass
class ImapParsedContext:
    uid: Optional[str] = None
    folder: Optional[str] = None


class ImapEmailAdapter:
    """Adapter to parse RFC822 bytes fetched from IMAP into ParsedMessage."""

    @staticmethod
    def parse_bytes(rfc822_bytes: bytes, recipient_address: str, ctx: Optional[ImapParsedContext] = None) -> ParsedMessage:
        msg: email.message.EmailMessage = email.message_from_bytes(rfc822_bytes, policy=policy.default)

        # Headers
        raw_from = _decode_header_value(msg.get("From"))
        sender_email = (parseaddr(raw_from)[1] or raw_from).strip()
        subject = _decode_header_value(msg.get("Subject"))
        message_id = (msg.get("Message-Id") or msg.get("Message-ID") or "").strip()
        references = _decode_header_value(msg.get("References"))

        # Body text selection
        body_text, body_used, body_html = _extract_text_parts(msg)

        # Strip forwards/replies if configured
        body_html_preserved = body_html
        if EMAIL_STRIP_REPLIES:
            is_forward = _is_forward_like(subject or "", body_text or "", [])
            if is_forward:
                body_html_preserved = None
                pre, fwd = _extract_forward_sections(body_text)
                if fwd and pre:
                    body_text = f"{pre}\n\n{fwd}"
                elif fwd:
                    body_text = fwd
                elif pre:
                    body_text = pre

        attachments, rejected_attachments = _collect_attachments(msg)

        # Build raw payload for diagnostics
        hdr_map: MutableMapping[str, str] = {}
        try:
            for k, v in msg.items():
                hdr_map[str(k)] = _decode_header_value(v)
        except Exception:
            pass

        raw_payload: MutableMapping[str, Any] = {
            "message_id": message_id,
            "references": references,
            "headers": hdr_map,
            "body_used": body_used,
        }
        if body_html_preserved:
            raw_payload["body_html"] = body_html_preserved
        if ctx is not None:
            if ctx.uid:
                raw_payload["imap_uid"] = str(ctx.uid)
            if ctx.folder:
                raw_payload["imap_folder"] = str(ctx.folder)
        if rejected_attachments:
            raw_payload["rejected_attachments"] = rejected_attachments

        return ParsedMessage(
            sender=sender_email,
            recipient=recipient_address,
            subject=subject,
            body=body_text or "",
            attachments=attachments,
            raw_payload=raw_payload,
            msg_channel=CommsChannel.EMAIL,
        )
