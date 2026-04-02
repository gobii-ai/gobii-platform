"""
Email sending tool for persistent agents.

This module provides email sending functionality for persistent agents,
including tool definition and execution logic.
"""

import logging
import re
from typing import Dict, Any

from ...models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    CommsChannel,
    DeliveryStatus,
)
from django.conf import settings
from ..comms.email_threading import (
    get_message_channel,
    get_message_contact_address,
    normalize_email_address,
)
from ..comms.outbound_delivery import deliver_agent_email
from ..comms.email_endpoint_routing import resolve_agent_email_sender_endpoint_for_message
from ..comms.message_service import _ensure_participant, _get_or_create_conversation
from .outbound_duplicate_guard import detect_recent_duplicate_message
from util.integrations import postmark_status
from util.text_sanitizer import decode_unicode_escapes, strip_control_chars
from .agent_variables import substitute_variables_with_filespace
from ..files.attachment_helpers import (
    AttachmentResolutionError,
    create_message_attachments,
    resolve_filespace_attachments,
)
from ..files.filespace_service import broadcast_message_attachment_update
from api.services.email_verification import require_verified_email, EmailVerificationError
from api.services.signup_preview import (
    can_bypass_email_verification_for_signup_preview_first_email,
)
from .attachment_guidance import SEND_EMAIL_ATTACHMENTS_DESCRIPTION

logger = logging.getLogger(__name__)


_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_QUOTED_THREAD_PATTERN = re.compile(r"<blockquote\b[^>]*>.*?</blockquote>", re.IGNORECASE | re.DOTALL)
_ATTACHMENT_CLAIM_PATTERNS = (
    re.compile(r"\bplease\s+find\s+attached\b", re.IGNORECASE),
    re.compile(r"\bsee\s+attached\b", re.IGNORECASE),
    re.compile(r"\b(?:i(?:'|’)ve|i\s+have)\s+attached\b", re.IGNORECASE),
    re.compile(r"\battached\s+(?:you(?:'|’)ll|you\s+will)\s+find\b", re.IGNORECASE),
    re.compile(r"\battached\s+(?:is|are)\b", re.IGNORECASE),
)
_MISSING_ATTACHMENT_CLAIM_ERROR_MESSAGE = (
    "Email body claims attachments are included, but send_email.attachments is empty. "
    "Pass the exact $[/path] values returned by recent file tools in send_email.attachments."
)


def _maybe_provision_simulated_from_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint | None:
    """Provision a local sender endpoint for dev simulation when real transport is unavailable."""
    simulation_flag = getattr(settings, "SIMULATE_EMAIL_DELIVERY", False)
    postmark_state = postmark_status()
    if not simulation_flag or postmark_state.enabled:
        return None

    from django.db import DatabaseError

    sim_address = f"agent-{agent.id}@localhost"
    try:
        endpoint = PersistentAgentCommsEndpoint.objects.create(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
            address=sim_address,
            is_primary=True,
        )
    except DatabaseError as exc:
        logger.exception(
            "Failed to provision simulated email endpoint for agent %s: %s",
            agent.id,
            exc,
        )
        return None

    logger.info(
        "Provisioned simulated from_endpoint %s for agent %s to enable local email simulation",
        sim_address,
        agent.id,
    )
    return endpoint


def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True if the caller indicated ongoing work after this send."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def _strip_html_to_text(html: str) -> str:
    """Convert lightweight HTML email content to plain text for semantic checks."""
    if not html:
        return ""
    return re.sub(r"\s+", " ", _HTML_TAG_PATTERN.sub(" ", html)).strip()


def _strip_quoted_thread_html(html: str) -> str:
    """Ignore quoted thread content so only newly authored attachment claims are enforced."""
    if not html:
        return ""
    return _QUOTED_THREAD_PATTERN.sub(" ", html)


def _email_claims_attachments(html: str) -> bool:
    """Return True when the email body explicitly claims attachments are included."""
    plain_text = _strip_html_to_text(_strip_quoted_thread_html(html))
    if not plain_text:
        return False
    return any(pattern.search(plain_text) for pattern in _ATTACHMENT_CLAIM_PATTERNS)


def _resolve_reply_target(
    agent: PersistentAgent,
    reply_to_message_id: str,
    normalized_to_address: str,
) -> tuple[PersistentAgentMessage | None, dict[str, Any] | None]:
    if not reply_to_message_id:
        return None, None

    try:
        target_message = (
            PersistentAgentMessage.objects
            .select_related("from_endpoint", "to_endpoint", "conversation")
            .get(id=reply_to_message_id, owner_agent=agent)
        )
    except PersistentAgentMessage.DoesNotExist:
        return None, {
            "status": "error",
            "message": "reply_to_message_id must reference one of this agent's email messages.",
        }

    if get_message_channel(target_message) != CommsChannel.EMAIL:
        return None, {
            "status": "error",
            "message": "reply_to_message_id must reference an email message.",
        }

    target_address = get_message_contact_address(target_message)
    if not target_address or target_address != normalized_to_address:
        return None, {
            "status": "error",
            "message": "reply_to_message_id does not match to_address.",
        }

    return target_message, None


def get_send_email_tool() -> Dict[str, Any]:
    """Return the send_email tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Sends an email to a recipient. Write the body as HTML email content. DO NOT include <html>, <head>, or <body> tags—the system will wrap your content. Avoid markdown formatting. If you need tabular data, use real HTML table tags like <table>, <tr>, <th>, and <td>; do NOT use Markdown pipe tables like | Col | Col |. Quote recent parts of the conversation when relevant. "
                "IMPORTANT: Use single quotes for ALL HTML attributes (e.g., <a href='https://example.com'>link</a>) to keep the JSON arguments valid. Do NOT use double quotes in HTML attributes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_address": {"type": "string", "description": "Recipient email."},
                    "cc_addresses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "format": "email",
                        },
                        "description": "List of CC email addresses (optional)"
                    },
                    "subject": {"type": "string", "description": "Email subject."},
                    "reply_to_message_id": {
                        "type": "string",
                        "description": (
                            "Optional internal Gobii message id for replying in an existing email thread. "
                            "Omit this to start a new thread. Pass the email message id from recent contacts "
                            "or unified history to reply in that thread."
                        ),
                    },
                    "mobile_first_html": {"type": "string", "description": "Email content as HTML, excluding <html>, <head>, and <body> tags. Use single quotes for attributes, e.g. <a href='https://news.ycombinator.com'>News</a>. Must be actual email content, NOT tool call syntax. XML like <function_calls> or <invoke> does NOT execute tools—it will be sent as literal text."},
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": SEND_EMAIL_ATTACHMENTS_DESCRIPTION,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done. Omitting this stops you for good—choose wisely.",
                    },
                },
                "required": ["to_address", "subject", "mobile_first_html", "will_continue_work"],
            },
        },
    }


def execute_send_email(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the send_email tool for a persistent agent."""
    if not can_bypass_email_verification_for_signup_preview_first_email(agent):
        try:
            require_verified_email(agent.user, action_description="send emails")
        except EmailVerificationError as e:
            return e.to_tool_response()

    to_address = normalize_email_address(params.get("to_address"))
    subject = params.get("subject")
    # Decode escape sequences and strip control chars from HTML body
    mobile_first_html = decode_unicode_escapes(params.get("mobile_first_html"))
    mobile_first_html = strip_control_chars(mobile_first_html)
    # Substitute $[var] placeholders with actual values (e.g., $[/charts/...]).
    mobile_first_html = substitute_variables_with_filespace(mobile_first_html, agent)
    cc_addresses = [normalize_email_address(addr) for addr in params.get("cc_addresses", [])]
    will_continue = _should_continue_work(params)
    attachment_paths = params.get("attachments")
    reply_to_message_id = str(params.get("reply_to_message_id") or "").strip()

    if not all([to_address, subject, mobile_first_html]):
        return {"status": "error", "message": "Missing required parameters: to_address, subject, or mobile_first_html"}

    if _email_claims_attachments(mobile_first_html) and not attachment_paths:
        return {"status": "error", "message": _MISSING_ATTACHMENT_CLAIM_ERROR_MESSAGE}

    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}

    # Log email attempt
    body_preview = mobile_first_html[:100] + "..." if len(mobile_first_html) > 100 else mobile_first_html
    cc_info = f", CC: {cc_addresses}" if cc_addresses else ""
    attachment_info = f", attachments: {len(resolved_attachments)}" if resolved_attachments else ""
    logger.info(
        "Agent %s sending email to %s%s%s, subject: '%s', body: %s",
        agent.id, to_address, cc_info, attachment_info, subject, body_preview
    )

    try:
        # Ensure a healthy DB connection for subsequent ORM ops
        from django.db import close_old_connections
        from django.db.utils import OperationalError
        all_recipients = [to_address] + cc_addresses
        for recipient in all_recipients:
            if not agent.is_recipient_whitelisted(CommsChannel.EMAIL, recipient):
                return {
                    "status": "error",
                    "message": (
                        f"Recipient address '{recipient}' not allowed for this agent. "
                        "You can request access by calling the request_contact_permission tool."
                    ),
                }

        close_old_connections()
        try:
            to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.EMAIL, address=to_address, defaults={"owner_agent": None}
            )
        except OperationalError:
            close_old_connections()
            to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.EMAIL, address=to_address, defaults={"owner_agent": None}
            )
        
        # Create CC endpoints
        cc_endpoint_objects = []
        for cc_addr in cc_addresses:
            close_old_connections()
            try:
                cc_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                    channel=CommsChannel.EMAIL, address=cc_addr, defaults={"owner_agent": None}
                )
                cc_endpoint_objects.append(cc_endpoint)
            except OperationalError:
                close_old_connections()
                cc_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                    channel=CommsChannel.EMAIL, address=cc_addr, defaults={"owner_agent": None}
                )
                cc_endpoint_objects.append(cc_endpoint)

        conversation = _get_or_create_conversation(
            CommsChannel.EMAIL,
            to_address,
            owner_agent=agent,
        )
        reply_target, reply_error = _resolve_reply_target(agent, reply_to_message_id, to_address)
        if reply_error:
            return reply_error

        duplicate = detect_recent_duplicate_message(
            agent,
            channel=CommsChannel.EMAIL,
            body=mobile_first_html,
            to_address=to_address,
            conversation_id=conversation.id,
        )
        if duplicate:
            return duplicate.to_error_response()

        from_endpoint = resolve_agent_email_sender_endpoint_for_message(
            agent,
            to_endpoint=to_endpoint,
            cc_endpoints=cc_endpoint_objects,
            has_bcc=False,
            log_context="send_email_tool",
        )
        if not from_endpoint:
            from_endpoint = _maybe_provision_simulated_from_endpoint(agent)
            if not from_endpoint:
                return {"status": "error", "message": "Agent has no configured email endpoint to send from."}

        _ensure_participant(
            conversation,
            from_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.AGENT,
        )
        _ensure_participant(
            conversation,
            to_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )

        close_old_connections()
        try:
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                conversation=conversation,
                parent=reply_target,
                is_outbound=True,
                body=mobile_first_html,
                raw_payload={"subject": subject},
            )
            # Add CC endpoints to the message
            if cc_endpoint_objects:
                message.cc_endpoints.set(cc_endpoint_objects)
            if resolved_attachments:
                create_message_attachments(message, resolved_attachments)
                broadcast_message_attachment_update(str(message.id))
        except OperationalError:
            close_old_connections()
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                conversation=conversation,
                parent=reply_target,
                is_outbound=True,
                body=mobile_first_html,
                raw_payload={"subject": subject},
            )
            # Add CC endpoints to the message
            if cc_endpoint_objects:
                message.cc_endpoints.set(cc_endpoint_objects)
            if resolved_attachments:
                create_message_attachments(message, resolved_attachments)
                broadcast_message_attachment_update(str(message.id))

        # Immediately attempt delivery
        deliver_agent_email(message)

        # Check the result
        close_old_connections()
        try:
            message.refresh_from_db()
        except OperationalError:
            close_old_connections()
            message.refresh_from_db()
        if message.latest_status == DeliveryStatus.FAILED:
            return {"status": "error", "message": f"Email failed to send: {message.latest_error_message}"}

        return {
            "status": "ok",
            "message": f"Email sent to {to_address}.",
            "message_id": str(message.id),
            "auto_sleep_ok": not will_continue,
        }

    except Exception as e:
        logger.exception("Failed to create and deliver email for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to send email: {e}"} 
