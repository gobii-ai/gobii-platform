"""Email sending tool for persistent agents."""

import logging
import re
from typing import Dict, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import close_old_connections, transaction
from django.db.utils import OperationalError

from ...models import (
    CommsAllowlistEntry,
    CommsChannel,
    DeliveryStatus,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
)
from ..comms.email_threading import get_message_channel, get_message_contact_address, normalize_email_address
from ..comms.outbound_delivery import deliver_agent_email
from ..comms.email_endpoint_routing import resolve_agent_email_sender_endpoint_for_message
from ..comms.message_service import _ensure_participant, _get_or_create_conversation
from .outbound_duplicate_guard import detect_recent_duplicate_message
from util.integrations import postmark_status
from util.text_sanitizer import decode_unicode_escapes, strip_control_chars
from .agent_variables import substitute_variables_with_filespace
from api.agent.core.link_references import handle_link_reference_errors
from ..files.attachment_helpers import AttachmentResolutionError, create_message_attachments, resolve_filespace_attachments
from ..files.filespace_service import broadcast_message_attachment_update
from api.services.email_verification import require_verified_email, EmailVerificationError
from api.services.signup_preview import can_bypass_email_verification_for_signup_preview_first_email
from api.services.contact_authorization import (
    AutomaticContactAuthorizationError,
    authorize_email_contacts,
)
from api.services.outbound_email_policy import (
    classify_email_recipients,
    email_review_outbox_enabled,
)
from api.services.outbound_email_review import queue_message_for_review
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
_MISSING_ATTACHMENT_CLAIM_ERROR_MESSAGE = "Email body claims attachments are included, but send_email.attachments is empty. Pass the exact $[/path] values returned by recent file tools in send_email.attachments."
_EMBEDDED_TOOL_ARGUMENT_PATTERN = re.compile(
    r"</[a-z][^>]*>\s*[\"']?\s*,(?=[\s\S]*[\"']will_continue_work[\"']\s*:\s*(?:true|false))[\s\S]*}\s*$",
    re.IGNORECASE | re.DOTALL,
)


class _EmailDeliveryFailed(Exception):
    pass


class _EmailMessageCreateOperationalError(Exception):
    pass


def _get_or_create_email_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    lookup = {
        "channel": CommsChannel.EMAIL,
        "address": address,
        "defaults": {"owner_agent": None},
    }
    close_old_connections()
    try:
        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(**lookup)
    except OperationalError:
        close_old_connections()
        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(**lookup)
    return endpoint


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


def _email_body_error(html: str, has_attachments: bool) -> str | None:
    if _EMBEDDED_TOOL_ARGUMENT_PATTERN.search(html or ""):
        return "Email body contains serialized sibling tool-call arguments. End mobile_first_html after the email content and pass every tool argument separately."
    plain_text = _strip_html_to_text(_strip_quoted_thread_html(html))
    if not has_attachments and any(pattern.search(plain_text) for pattern in _ATTACHMENT_CLAIM_PATTERNS):
        return _MISSING_ATTACHMENT_CLAIM_ERROR_MESSAGE
    return None


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
    return {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Body-only HTML; no document tags, Markdown, or long dashes. No <style> blocks/classes; inline CSS only. "
                "Approval or preparation is not sent: send, then record returned delivery_status; never infer delivered. "
                "pending_approval awaits approval; never retry. "
                "Reports: distinct styled sections/tables, accented headline metric, tasteful icon marker and obvious inline-styled badge. Never leave metrics in plain lists or use Markdown pipe tables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_address": {"type": "string", "format": "email", "description": "Email address; never an agent/user ID."},
                    "cc_addresses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "format": "email",
                        },
                        "description": "CC emails, never IDs. Replies inherit none; include anyone described as copied.",
                    },
                    "bcc_addresses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "format": "email",
                        },
                        "description": "Hidden recipients, never agent/user IDs; kept in owner audit, hidden from To/CC.",
                    },
                    "subject": {"type": "string", "description": "Email subject."},
                    "reply_to_message_id": {
                        "type": "string",
                        "description": (
                            "Optional internal Gobii message id for replying in-thread; omit to start a new thread."
                        ),
                    },
                    "mobile_first_html": {
                        "type": "string",
                        "description": (
                            "HTML body only; no <html>/<head>/<body>. Single-quoted attrs. "
                            "Reports need an accented headline metric, styled tables or metric blocks, and visible badges/icons. "
                            "Tool-call/XML is literal. Inline images: attach file + <img src='cid:filename'>."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": SEND_EMAIL_ATTACHMENTS_DESCRIPTION,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": (
                            "REQUIRED. false when this email is the requested final delivery, including 'send this now', reports, dashboards, summaries, outreach, or one-off replies. "
                            "true only when another immediate user-requested tool action must happen after the email; future scheduled work does not count."
                        ),
                    },
                },
                "required": ["to_address", "subject", "mobile_first_html", "will_continue_work"],
            },
        },
    }


@handle_link_reference_errors
def execute_send_email(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    if not can_bypass_email_verification_for_signup_preview_first_email(agent):
        try:
            require_verified_email(agent.user, action_description="send emails")
        except EmailVerificationError as e:
            return e.to_tool_response()

    to_address = normalize_email_address(params.get("to_address"))
    subject = params.get("subject")
    mobile_first_html = decode_unicode_escapes(params.get("mobile_first_html"))
    mobile_first_html = strip_control_chars(mobile_first_html)
    mobile_first_html = substitute_variables_with_filespace(mobile_first_html, agent)
    cc_addresses = [normalize_email_address(addr) for addr in params.get("cc_addresses", [])]
    bcc_addresses = [normalize_email_address(addr) for addr in params.get("bcc_addresses", [])]
    will_continue = _should_continue_work(params)
    attachment_paths = params.get("attachments")
    reply_to_message_id = str(params.get("reply_to_message_id") or "").strip()

    if not all([to_address, subject, mobile_first_html]):
        return {"status": "error", "message": "Missing required parameters: to_address, subject, or mobile_first_html"}

    for address in [to_address, *cc_addresses, *bcc_addresses]:
        try:
            validate_email(address)
        except ValidationError:
            return {
                "status": "error",
                "message": (
                    f"Recipient '{address}' is not a valid email address. "
                    "Use an actual email address, never an agent or user ID."
                ),
            }

    if body_error := _email_body_error(mobile_first_html, bool(attachment_paths)):
        return {"status": "error", "message": body_error}

    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}

    body_preview = mobile_first_html[:100] + "..." if len(mobile_first_html) > 100 else mobile_first_html
    cc_info = f", CC: {cc_addresses}" if cc_addresses else ""
    bcc_info = f", BCC count: {len(bcc_addresses)}" if bcc_addresses else ""
    attachment_info = f", attachments: {len(resolved_attachments)}" if resolved_attachments else ""
    logger.info(
        "Agent %s sending email to %s%s%s, subject: '%s', body: %s",
        agent.id, to_address, cc_info + bcc_info, attachment_info, subject, body_preview
    )

    try:
        all_recipients = [to_address] + cc_addresses + bcc_addresses
        outbox_enabled = email_review_outbox_enabled(agent.user)
        policy_decision = classify_email_recipients(agent, all_recipients) if outbox_enabled else None
        if policy_decision and policy_decision.blocked_recipients:
            blocked = policy_decision.blocked_recipients[0]
            return {
                "status": "error",
                "message": (
                    f"Outbound email is disabled for contact '{blocked}'. "
                    "The owner can enable it in Contacts & Access."
                ),
            }

        requires_review = bool(policy_decision and policy_decision.requires_review)
        should_auto_authorize = (
            agent.contact_approval_mode == PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
        )
        if should_auto_authorize:
            try:
                authorize_email_contacts(agent, all_recipients)
            except AutomaticContactAuthorizationError as exc:
                return {"status": "error", "message": str(exc)}

        # Contact authorization and email review are independent. An Outbox
        # decision must never grant permission to contact a new recipient.
        for recipient in all_recipients:
            if policy_decision and recipient in policy_decision.internal_recipients:
                continue
            if not agent.is_recipient_whitelisted(CommsChannel.EMAIL, recipient):
                if CommsAllowlistEntry.objects.filter(
                    agent=agent,
                    channel=CommsChannel.EMAIL,
                    address=recipient,
                    is_active=True,
                ).exists():
                    return {
                        "status": "error",
                        "message": (
                            f"Outbound email is disabled for contact '{recipient}'. "
                            "The owner can enable it in Contacts & Access."
                        ),
                    }
                return {
                    "status": "error",
                    "message": (
                        f"Recipient address '{recipient}' not allowed for this agent. "
                        "You can request access by calling the request_contact_permission tool."
                    ),
                }

        to_endpoint = _get_or_create_email_endpoint(to_address)
        cc_endpoint_objects = [_get_or_create_email_endpoint(address) for address in cc_addresses]
        bcc_endpoint_objects = [_get_or_create_email_endpoint(address) for address in bcc_addresses]

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
            has_bcc=bool(bcc_endpoint_objects),
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
        def _create_message():
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                conversation=conversation,
                parent=reply_target,
                is_outbound=True,
                body=mobile_first_html,
                raw_payload={"subject": subject},
            )
            if cc_endpoint_objects:
                message.cc_endpoints.set(cc_endpoint_objects)
            if bcc_endpoint_objects:
                message.bcc_endpoints.set(bcc_endpoint_objects)
            if resolved_attachments:
                create_message_attachments(message, resolved_attachments)
            return message

        def _create_and_deliver_message():
            with transaction.atomic():
                try:
                    message = _create_message()
                except OperationalError as exc:
                    raise _EmailMessageCreateOperationalError from exc

                if requires_review:
                    review = queue_message_for_review(message)
                    return message, review

                deliver_agent_email(message)

                # deliver_agent_email updates this instance before returning; checking it here lets
                # the transaction roll back before message-created on_commit handlers can run.
                if message.latest_status == DeliveryStatus.FAILED:
                    raise _EmailDeliveryFailed(message.latest_error_message)
                return message, None

        try:
            try:
                message, review = _create_and_deliver_message()
            except _EmailMessageCreateOperationalError:
                close_old_connections()
                message, review = _create_and_deliver_message()
        except _EmailDeliveryFailed as exc:
            return {"status": "error", "message": f"Email failed to send: {exc}"}

        close_old_connections()
        if resolved_attachments:
            broadcast_message_attachment_update(str(message.id))

        if review is not None:
            return {
                "status": "pending_approval",
                "delivery_status": "not_sent",
                "message": (
                    "Email placed in Outbox for approval. The recipient has not received it. "
                    "Do not retry or claim it was sent."
                ),
                "message_id": str(message.id),
                "outbox_item_id": str(review.id),
                "auto_sleep_ok": False,
            }

        return {
            "status": "ok",
            "delivery_status": message.latest_status,
            "message": f"Email send completed for {to_address} with delivery_status={message.latest_status}. Use that status exactly; do not claim recipient delivery unless it is delivered.",
            "message_id": str(message.id),
            "auto_sleep_ok": not will_continue,
        }

    except Exception as e:
        logger.exception("Failed to create and deliver email for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to send email: {e}"} 
