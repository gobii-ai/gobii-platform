"""Web chat sender tool for persistent agents."""

import re
from typing import Any, Dict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from ..comms.message_service import _get_or_create_conversation, _ensure_participant
from ..files.attachment_helpers import (
    AttachmentResolutionError,
    create_message_attachments,
    resolve_filespace_attachments,
)
from ..files.filespace_service import broadcast_message_attachment_update
from util.text_sanitizer import normalize_llm_output
from .agent_variables import substitute_variables_with_filespace
from ...models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    DeliveryStatus,
    CommsChannel,
    build_web_agent_address,
    build_web_user_address,
    parse_web_user_address,
)
from ...services.email_verification import has_verified_email
from ...services.web_sessions import get_deliverable_web_session
from .outbound_duplicate_guard import detect_recent_duplicate_message

_PROGRESS_PREFIX_RE = re.compile(
    r"^(?:(?:good|great|okay|ok|alright|sure)[,! ]+)?(?:now\s+)?"
    r"(?:let me|i(?:'ll| will| am going to| want to| need to)|i'm going to)\s+"
    r"(?:start|begin|continue|check|fetch|pull|look|search|research|extract|compile|process|analy[sz]e|verify|"
    r"inspect|scrape|organize|build|create|prepare|generate|run|hit|parse|get|format|summarize|structure|try)\b",
    re.IGNORECASE,
)
_INTERNAL_PROGRESS_RE = re.compile(
    r"\b(?:the user|already greeted|actual research|tool|tools|parallel|compile the results|extract the data|"
    r"mark the plan complete|plan complete|delivered message|wrap up|left the last cycle mid-stream|"
    r"deliver the final report now|want to verify|actually scraping|scrape results|inspect the actual|"
    r"real data is coming back|got what i need|let me also grab|let me send it over|let me end planning|"
    r"i now have (?:detailed )?data|source pages|mark the research steps|deliver the synthesized|"
    r"good (?:initial )?data gathered|let me (?:now )?scrape|let me do (?:a couple|some) more|"
    r"strengthen the competitive analysis|then synthesize|synthesize the full memo|"
    r"already have [^.?!]{0,80}\bdata)\b",
    re.IGNORECASE,
)
_OPTIONAL_PROGRESS_QUESTION_RE = re.compile(
    r"\b(?:any tweaks|any changes|anything to adjust|otherwise\b|if not\b|unless you want)\b",
    re.IGNORECASE,
)
_TRAILING_OPTIONAL_FOLLOWUP_RE = re.compile(
    r"[\s—–-]+(?:want me to|would you like me to|do you want me to|should i|shall i)\b[^?\n]{0,240}\?\s*[^\w\s]*\s*$",
    re.IGNORECASE,
)
PLACEHOLDER_MESSAGE_BODIES = {
    "body",
    "message",
    "text",
    "content",
    "string",
    "your message here",
}


def _looks_like_placeholder_body(body: str) -> bool:
    normalized = re.sub(r"\s+", " ", (body or "").strip().lower())
    return normalized in PLACEHOLDER_MESSAGE_BODIES


_TOOL_CALL_MARKUP_RE = re.compile(
    r"<\s*(?:function|function_calls|invoke|parameter)\b|"
    r"<\s*endor_thinking\s*>|"
    r"<\uff5cDSML\uff5c(?:function_calls|invoke|parameter)\b",
    re.IGNORECASE,
)


def _looks_like_tool_call_markup(body: str) -> bool:
    return bool(_TOOL_CALL_MARKUP_RE.search(body or ""))


def _strip_trailing_optional_followup(body: str) -> str:
    return _TRAILING_OPTIONAL_FOLLOWUP_RE.sub("", body or "").rstrip()


_TOOL_FRUSTRATION_PROGRESS_RE = re.compile(
    r"\b(?:fabricated test data|eval environment|stop fighting the sim|pivot hard|trying every tool|"
    r"same fabricated|same data set|same simulated results|simulated results|instructions say|"
    r"stop verifying|let me deliver|all done)\b",
    re.IGNORECASE,
)


def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True if the agent indicates more work right after this chat message."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def _latest_inbound_timestamp(agent: PersistentAgent):
    latest_inbound = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=False)
        .order_by("-timestamp")
        .values_list("timestamp", flat=True)
        .first()
    )
    return latest_inbound


def _has_outbound_since_latest_inbound(agent: PersistentAgent) -> bool:
    latest_inbound_at = _latest_inbound_timestamp(agent)
    if latest_inbound_at is None:
        return False
    return PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
        timestamp__gt=latest_inbound_at,
    ).exists()


def _looks_like_routine_progress_message(body: str) -> bool:
    text = " ".join((body or "").split())
    if not text:
        return False
    lower = text.lower()
    if _TOOL_FRUSTRATION_PROGRESS_RE.search(text):
        return True
    progress_signal = bool(_PROGRESS_PREFIX_RE.search(text) or _INTERNAL_PROGRESS_RE.search(text))
    if "?" in text and _OPTIONAL_PROGRESS_QUESTION_RE.search(text):
        return progress_signal
    if any(marker in lower for marker in ("as requested", "you asked", "blocking", "blocked", "?")):
        return False
    return progress_signal


def has_other_contact_channel(agent: PersistentAgent, recipient_user) -> bool:
    if has_verified_email(recipient_user):
        if PersistentAgentCommsEndpoint.objects.filter(
            owner_agent=agent,
            channel=CommsChannel.EMAIL,
        ).exists():
            return True
    if PersistentAgentCommsEndpoint.objects.filter(
        owner_agent=agent,
        channel=CommsChannel.SMS,
    ).exists():
        from api.models import UserPhoneNumber

        return UserPhoneNumber.objects.filter(
            user=recipient_user,
            is_verified=True,
        ).exists()
    return False


def get_send_chat_tool() -> Dict[str, Any]:
    """Definition for the send_chat_message tool exposed to the agent."""

    return {
        "type": "function",
        "function": {
            "name": "send_chat_message",
            "description": (
                "Send a user-facing web chat message for non-blocking context, config changes, or findings. "
                "Do not use this for questions that block the task; use request_human_input so the question is tracked. "
                "Do not narrate what you will do next after an acknowledgement, and do not send routine progress narration such as "
                "tool sequencing, plan mechanics, or internal reasoning."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": "Actual user-facing chat text. Do not pass schema placeholders like 'body', 'message', or 'text'. Do not pass tool-call syntax; XML like <function_calls> or <invoke> is sent literally, not executed.",
                    },
                    "to_address": {
                        "type": "string",
                        "description": (
                            "Optional web chat address for the recipient (e.g. 'web://user/123/agent/<agent_id>'). "
                            "If omitted, the agent will reply to the latest active chat participant or preferred web contact. "
                            "If no other communication channels are available, it will default to the owner's web address."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of filespace paths or $[/path] variables to include. Pass attachments here; do not paste file paths into the chat body unless you want them shown as text.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = continue after this user-facing message; false = stop. Never send a message solely to justify continuing work.",
                    },
                },
                "required": ["body", "will_continue_work"],
            },
        },
    }


def execute_send_chat_message(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an outbound web chat message for an agent."""

    raw_body = params.get("body", "")
    # Normalize LLM output: decode escapes, strip control chars, normalize whitespace
    body = normalize_llm_output((raw_body or "").strip())
    # Substitute $[var] placeholders with actual values (e.g., $[/charts/...]).
    body = substitute_variables_with_filespace(body, agent)
    if not body:
        return {"status": "error", "message": "Message body is required."}
    if _looks_like_placeholder_body(body):
        return {
            "status": "error",
            "message": "Message body must contain actual user-facing content, not a schema placeholder.",
            "retryable": False,
        }
    if _looks_like_tool_call_markup(body):
        return {
            "status": "error",
            "message": (
                "Message body must contain actual user-facing content, not raw tool-call markup. "
                "Use the tool_calls field to invoke tools."
            ),
            "retryable": False,
        }
    will_continue = _should_continue_work(params)
    if not will_continue:
        body = _strip_trailing_optional_followup(body)
        if not body:
            return {"status": "error", "message": "Message body is required after removing optional follow-up."}
    if will_continue and _looks_like_routine_progress_message(body):
        return {
            "status": "ok",
            "message": "Skipped routine progress-only chat message.",
            "auto_sleep_ok": False,
            "skipped": True,
        }
    attachment_paths = params.get("attachments")
    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}

    max_len = getattr(settings, "WEB_CHAT_MESSAGE_MAX_LENGTH", 4000)
    if len(body) > max_len:
        return {
            "status": "error",
            "message": f"Chat message exceeds maximum length of {max_len} characters.",
        }

    to_address = (params.get("to_address") or "").strip()

    if not to_address:
        # Prefer explicit preferred endpoint configured for web chat
        if agent.preferred_contact_endpoint and agent.preferred_contact_endpoint.channel == CommsChannel.WEB:
            to_address = agent.preferred_contact_endpoint.address
        else:
            latest_conversation = agent.owned_conversations.filter(channel=CommsChannel.WEB).order_by("-id").first()
            if latest_conversation:
                to_address = latest_conversation.address
            else:
                owner_user = getattr(agent, "user", None)
                if owner_user and not has_other_contact_channel(agent, owner_user):
                    # When web chat is the only channel, default to the owner's web address.
                    to_address = build_web_user_address(owner_user.id, agent.id)

    if not to_address:
        return {
            "status": "error",
            "message": "No eligible web chat recipient found. Provide 'to_address'.",
        }

    user_id, agent_id = parse_web_user_address(to_address)
    if agent_id != str(agent.id) or user_id is None:
        return {
            "status": "error",
            "message": "Recipient address is not valid for this agent.",
        }

    # Check if this is a normal user interaction or a test/eval interaction
    is_eval_mode = (agent.execution_environment == "eval")

    if not is_eval_mode:
        User = get_user_model()
        try:
            recipient_user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            recipient_user = None

        if not recipient_user:
            return {
                "status": "error",
                "message": (
                    "No active web chat session exists for this user. Retry using the user's most recently "
                    "active non-web communication channel (e.g., email or SMS)."
                ),
            }

        # If the user has other communication channels, we want to ensure we're sending to an active chat session
        # If the user does not have other communication channels, pass through to web because it's our only choice
        if (
            get_deliverable_web_session(agent, recipient_user) is None
            and has_other_contact_channel(agent, recipient_user)
        ):
            return {
                "status": "error",
                "message": (
                    "No active web chat session exists for this user. Retry using the user's most recently "
                    "active non-web communication channel (e.g., email or SMS)."
                ),
            }

        if not agent.is_recipient_whitelisted(CommsChannel.WEB, to_address):
            return {
                "status": "error",
                "message": "Recipient is not authorized for web chat with this agent.",
            }

    agent_endpoint = _ensure_agent_web_endpoint(agent)
    user_endpoint = _ensure_user_web_endpoint(to_address)

    conversation = _get_or_create_conversation(CommsChannel.WEB, to_address, owner_agent=agent)
    _ensure_participant(
        conversation,
        agent_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.AGENT,
    )
    _ensure_participant(
        conversation,
        user_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.HUMAN_USER,
    )

    duplicate = detect_recent_duplicate_message(
        agent,
        channel=CommsChannel.WEB,
        body=body,
        conversation_id=conversation.id,
    )
    if duplicate:
        return duplicate.to_error_response()

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=agent_endpoint,
        to_endpoint=user_endpoint,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload={"source": "web_chat_tool"},
    )
    if resolved_attachments:
        create_message_attachments(message, resolved_attachments)
        broadcast_message_attachment_update(str(message.id))

    now = timezone.now()
    PersistentAgentMessage.objects.filter(pk=message.pk).update(
        latest_status=DeliveryStatus.DELIVERED,
        latest_sent_at=now,
        latest_delivered_at=now,
        latest_error_code="",
        latest_error_message="",
    )
    from api.agent.tasks.process_events import schedule_unseen_web_chat_followup

    schedule_unseen_web_chat_followup(message)

    return {
        "status": "ok",
        "message": f"Web chat message sent to {to_address}",
        "message_id": str(message.id),
        "auto_sleep_ok": not will_continue,
    }


def _ensure_agent_web_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
    """Ensure the agent has a dedicated web chat endpoint."""

    address = build_web_agent_address(agent.id)
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        owner_agent=agent,
        channel=CommsChannel.WEB,
        address=address,
        defaults={
            "is_primary": bool(
                agent.preferred_contact_endpoint
                and agent.preferred_contact_endpoint.channel == CommsChannel.WEB
            ),
        },
    )

    return endpoint


def _ensure_user_web_endpoint(address: str) -> PersistentAgentCommsEndpoint:
    """Ensure an external participant endpoint exists for the given web chat address."""

    normalized = (address or "").strip()
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.WEB,
        address=normalized,
        defaults={"owner_agent": None},
    )
    return endpoint
