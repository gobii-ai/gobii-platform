"""Web chat sender tool for persistent agents."""

import re
from typing import Any, Dict

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Max
from django.utils import timezone

from ..comms.message_service import _get_or_create_conversation, _ensure_participant
from ..comms.message_reads import is_peer_dm_message
from ..comms.routing import get_current_inbound_message, get_message_sender_address
from ..files.attachment_helpers import AttachmentResolutionError, create_message_attachments, resolve_filespace_attachments
from ..files.filespace_service import broadcast_message_attachment_update
from util.text_sanitizer import normalize_llm_output
from .agent_variables import substitute_variables_with_filespace
from api.agent.core.link_references import handle_link_reference_errors
from .attachment_guidance import SEND_TOOL_ATTACHMENTS_DESCRIPTION
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
from ...services.web_sessions import get_deliverable_web_session, get_deliverable_web_sessions
from .outbound_duplicate_guard import detect_recent_duplicate_message

WEB_CHAT_UNAVAILABLE_MESSAGE = "No active web chat session exists for this requester. Do not move this reply to email, SMS, or another channel."
BACKGROUND_WEB_UNAVAILABLE_MESSAGE = "No active web chat session exists for this recipient. Use an available configured delivery channel."
WRONG_INBOUND_CHANNEL_MESSAGE = (
    "The current request did not arrive in web chat. Reply on its inbound channel with the matching send tool; "
    "do not provide another web target."
)
PEER_INBOUND_CHANNEL_MESSAGE = "The current request is a peer DM. Use send_agent_message only if a reply is needed; do not provide a web target."
_CURRENT_INBOUND_UNSET = object()

_PROGRESS_PREFIX_RE = re.compile(
    r"^(?:(?:good|great|okay|ok|alright|sure)[,! ]+)?(?:now\s+)?"
    r"(?:let me|i(?:'ll| will| am going to| want to| need to)|i'm going to)\s+"
    r"(?:(?:actually|first|just|quickly|then|also)\s+)?"
    r"(?:(?:do\s+)?(?:start|begin|continue|check|fetch|find|grab|investigate|pull|look|search|research|extract|compile|process|analy[sz]e|verify)|"
    r"do\s+(?:proper\s+|additional\s+|more\s+|some\s+|a\s+few\s+|new\s+)?(?:search(?:es)?|queries|lookups?)|"
    r"inspect|scrape|organize|build|create|prepare|generate|run|hit|parse|get|read|patch|rewrite|seed|register|format|summarize|structure|try)\b",
    re.IGNORECASE,
)
_INTERNAL_PROGRESS_RE = re.compile(
    r"\b(?:the user|already greeted|actual research|parallel|compile the results|extract the data|"
    r"mark the plan complete|plan complete|delivered message|wrap up|left the last cycle mid-stream|"
    r"deliver the final report now|want to verify|actually scraping|scrape results|inspect the actual|"
    r"real data is coming back|got what i need|let'?s (?:dig up|fetch|find|get|grab|look up|pull|research|search)|let me (?:also |now |actually |just |quickly |then )?(?:grab|fetch|find|investigate|check|pull|get|look|search|research|query|verify|analy[sz]e|compile|process|inspect|fix|patch|clean(?: up)?|seed|register|do (?:a |the |thorough |proper |additional |more |some |a few |new )?(?:search(?:es)?|queries|lookups?|cleanup|clean up))|let me send it over|let me end planning|"
    r"got (?:the )?(?:result|results|data|source material).{0,180}\blet me (?:report|send|share|set up|configure)|"
    r"i now have (?:detailed )?(?:data|source pages)|mark the research steps|deliver the synthesized|"
    r"good (?:initial )?data gathered|let me (?:now )?scrape|let me do (?:a couple|some) more|"
    r"strengthen the competitive analysis|then synthesize|synthesize the full memo|"
    r"already have [^.?!]{0,80}\bdata)\b",
    re.IGNORECASE,
)
_RECOVERY_THEN_PROGRESS_RE = re.compile(
    r"\b(?:query|queries|tool|tools|cte|json_extract|sqlite|auto-correction|correction|schema|path|data)\b.{0,140}"
    r"\b(?:error|failed|failing|hitting|kept hitting|did not work|spurious|wrong|incorrect)\b.{0,180}"
    r"\b(?:let me|i(?:'ll| will| need to| am going to)|next|then)\s+"
    r"(?:extract|query|try|inspect|use|build|create|drop|recreate|rerun|report|summari[sz]e)\b",
    re.IGNORECASE,
)
_OPTIONAL_PROGRESS_QUESTION_RE = re.compile(
    r"\b(?:any tweaks|any changes|anything to adjust|otherwise\b|if not\b|unless you want)\b",
    re.IGNORECASE,
)
_RESULTS_STATUS_PROGRESS_RE = re.compile(
    r"^(?:(?:good|great|okay|ok|alright|sure)[,! ]+)?"
    r"(?:(?:i(?:'ve)?|we)\s+(?:now\s+)?(?:have|found|got)\s+(?:the\s+)?(?:search\s+)?(?:result|results|data|sources?)|all\s+(?:\w+|\d+)(?:\s+[\w-]+){0,2}\s+(?:sources?|pages?|results?|endpoints?|urls?)\s+(?:are|were)\s+(?:fetched|scraped|loaded|processed|done)|the\s+data\s+is\s+in)\b",
    re.IGNORECASE,
)
_RETURNED_DATA_THEN_PROGRESS_RE = re.compile(
    r"\b(?:site|page|api|browser task|tool|source|result)\b.{0,100}"
    r"\b(?:returned|found|provided|gave|has)\b.{0,80}\b(?:data|result|results)\b.{0,160}"
    r"\b(?:let me|i(?:'ll| will| need to| am going to)|next|then)\s+"
    r"(?:update|set up|configure|report|send|deliver|share|write|summari[sz]e)\b",
    re.IGNORECASE,
)
_FORWARD_PROGRESS_ACTION_RE = re.compile(
    r"\b(?:let me|i(?:'ll| will| need to| can| am going to)|next|then)\s+"
    r"(?:open|scrape|fetch|query|read|review|use|analy[sz]e|synthesi[sz]e|compile|prepare|write|send|deliver|report|summari[sz]e|extract|check|update|configure|set up)\b",
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


def _strip_leading_routine_preamble(body: str) -> str:
    preamble, separator, report = (body or "").partition("\n\n")
    if not separator or not report.strip() or not _looks_like_routine_progress_message(preamble):
        return body
    if not re.match(r"\s*(?:#{1,6}\s|\*\*[^*\n]+\*\*)", report):
        return body
    return report.lstrip()


_TOOL_FRUSTRATION_PROGRESS_RE = re.compile(
    r"\b(?:fabricated(?:\b| (?:test data|links|results))|fake (?:job ids|links|data|results)|eval environment|stop fighting the sim|pivot hard|trying every tool|"
    r"same fabricated|same data set|same simulated results|simulated results|instructions say|"
    r"stop verifying|let me deliver)\b",
    re.IGNORECASE,
)


def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True if the agent indicates more work right after this chat message."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)


def _looks_like_routine_progress_message(body: str) -> bool:
    text = " ".join((body or "").split())
    if not text:
        return False
    lower = text.lower()
    if _TOOL_FRUSTRATION_PROGRESS_RE.search(text):
        return True
    if _RECOVERY_THEN_PROGRESS_RE.search(text):
        return True
    if _RETURNED_DATA_THEN_PROGRESS_RE.search(text):
        return True
    result_status = bool(_RESULTS_STATUS_PROGRESS_RE.search(text))
    if result_status and not re.search(r"\bhere(?:'s| is) (?:the )?(?:analysis|answer|recommendation|report)\b", text, re.I) and (
        _FORWARD_PROGRESS_ACTION_RE.search(text)
        or ("http://" not in lower and "https://" not in lower and len(text) <= 500)
    ):
        return True
    progress_signal = bool(_PROGRESS_PREFIX_RE.search(text) or _INTERNAL_PROGRESS_RE.search(text))
    if progress_signal and re.search(r"\b(?:claims extracted|strongest unique claims|source urls?)\b|(?:^|\s)\|[^|]+\|", text, re.I):
        return False
    if "?" in text and _OPTIONAL_PROGRESS_QUESTION_RE.search(text):
        return progress_signal
    if any(marker in lower for marker in ("as requested", "you asked", "blocking", "blocked", "?")):
        return False
    return progress_signal


def _looks_like_stop_marked_progress_message(body: str) -> bool:
    text = " ".join((body or "").split())
    if not text:
        return False
    lower = text.lower()
    if (
        _TOOL_FRUSTRATION_PROGRESS_RE.search(text)
        or _RECOVERY_THEN_PROGRESS_RE.search(text)
        or _RETURNED_DATA_THEN_PROGRESS_RE.search(text)
    ):
        return True
    result_status = bool(_RESULTS_STATUS_PROGRESS_RE.search(text))
    return result_status and (
        _FORWARD_PROGRESS_ACTION_RE.search(text)
        or ("http://" not in lower and "https://" not in lower and len(text) <= 500)
    )


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


def _resolve_default_web_chat_address(agent: PersistentAgent, *, current_inbound=_CURRENT_INBOUND_UNSET) -> str:
    latest_inbound = (
        get_current_inbound_message(agent)
        if current_inbound is _CURRENT_INBOUND_UNSET
        else current_inbound
    )
    if latest_inbound:
        if is_peer_dm_message(latest_inbound) or latest_inbound.conversation.channel != CommsChannel.WEB:
            return ""
        latest_address = get_message_sender_address(latest_inbound)
        if agent.is_recipient_whitelisted(CommsChannel.WEB, latest_address):
            return latest_address
        return ""

    if agent.preferred_contact_endpoint and agent.preferred_contact_endpoint.channel == CommsChannel.WEB:
        return agent.preferred_contact_endpoint.address

    for session in get_deliverable_web_sessions(agent):
        if session.user_id is not None:
            session_address = build_web_user_address(session.user_id, agent.id)
            if agent.is_recipient_whitelisted(CommsChannel.WEB, session_address):
                return session_address

    web_conversations = agent.owned_conversations.filter(channel=CommsChannel.WEB)
    messaged_conversations = (
        web_conversations.filter(messages__isnull=False)
        .annotate(latest_message_at=Max("messages__timestamp"))
        .order_by("-latest_message_at", "-id")
    )
    for conversation in messaged_conversations:
        if agent.is_recipient_whitelisted(CommsChannel.WEB, conversation.address):
            return conversation.address

    for conversation in web_conversations.order_by("-id"):
        if agent.is_recipient_whitelisted(CommsChannel.WEB, conversation.address):
            return conversation.address

    owner_user = getattr(agent, "user", None)
    if owner_user and not web_conversations.exists() and not has_other_contact_channel(agent, owner_user):
        return build_web_user_address(owner_user.id, agent.id)

    return ""


def _web_unavailable_result(*, current_requester: bool, will_continue: bool) -> dict[str, Any]:
    if current_requester:
        return {
            "status": "error",
            "message": WEB_CHAT_UNAVAILABLE_MESSAGE,
            "retryable": False,
            "terminal_error": not will_continue,
        }
    return {
        "status": "error",
        "message": BACKGROUND_WEB_UNAVAILABLE_MESSAGE,
        "retryable": False,
    }


def get_send_chat_tool() -> Dict[str, Any]:
    """Definition for the send_chat_message tool exposed to the agent."""

    return {
        "type": "function",
        "function": {
            "name": "send_chat_message",
            "description": (
                "Send web chat for user-facing content. "
                "Never ask a recipient to paste, send, or otherwise provide credential values; call secure_credentials_request and send only its secure entry URL. "
                "Deep/exhaustive, large-batch, large implementation/deployment, or explicitly long work only: send scope + next checkpoint FIRST with will_continue_work=true, then one material milestone. "
                "Do not use this to simulate or confirm an email/SMS delivery; if the user asked to email or text and the send_email/send_sms tool is available, call that tool instead. "
                "No generic or internal progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": (
                            "Natural recipient text; no dash punctuation between phrases. Keep chat/outreach light. Reports comparing 4+ peers use one table. "
                            "Do not pass tool-call/XML syntax; it is sent literally."
                        ),
                    },
                    "to_address": {
                        "type": "string",
                        "description": (
                            "Optional web chat address; omit to reply to the latest web requester."
                        ),
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": SEND_TOOL_ATTACHMENTS_DESCRIPTION,
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true=another immediate tool call follows in this turn; false=current turn is done, even if future scheduled work remains, and no current plan items remain unfinished. Never send a message solely to justify continuing work.",
                    },
                },
                "required": ["body", "will_continue_work"],
            },
        },
    }


@handle_link_reference_errors
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
    if agent.execution_environment == "eval" and will_continue:
        return {
            "status": "ok",
            "message": (
                "Recorded in-progress chat message for eval; do not repeat it. Continue the work, then deliver the substantive reply "
                "in this web chat; do not switch to email or SMS unless the user requested it."
            ),
            "auto_sleep_ok": False,
            "skipped": True,
        }
    if not will_continue:
        body = _strip_leading_routine_preamble(body)
        body = _strip_trailing_optional_followup(body)
        if not body:
            return {"status": "error", "message": "Message body is required after removing optional follow-up."}
    if _looks_like_routine_progress_message(body) and (
        will_continue or _looks_like_stop_marked_progress_message(body)
    ):
        return {
            "status": "ok",
            "message": (
                "Skipped routine progress-only chat message. Continue the work, then deliver the substantive reply "
                "in this web chat; do not switch to email or SMS unless the user requested it."
            ),
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

    current_inbound = get_current_inbound_message(agent)
    current_requester_address = ""
    current_inbound_is_web = False
    if (
        current_inbound is not None
        and not is_peer_dm_message(current_inbound)
        and current_inbound.conversation.channel == CommsChannel.WEB
    ):
        current_inbound_is_web = True
        current_requester_address = get_message_sender_address(current_inbound)

    to_address = (params.get("to_address") or "").strip()

    if not to_address:
        if current_inbound is not None and not current_inbound_is_web:
            return {
                "status": "error",
                "message": (
                    PEER_INBOUND_CHANNEL_MESSAGE
                    if is_peer_dm_message(current_inbound)
                    else WRONG_INBOUND_CHANNEL_MESSAGE
                ),
                "retryable": False,
            }
        to_address = _resolve_default_web_chat_address(agent, current_inbound=current_inbound)

    is_current_requester = bool(current_requester_address and to_address == current_requester_address)

    if not to_address:
        return _web_unavailable_result(
            current_requester=current_inbound_is_web,
            will_continue=will_continue,
        )

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
            return _web_unavailable_result(
                current_requester=is_current_requester,
                will_continue=will_continue,
            )

        if (
            not is_current_requester
            and get_deliverable_web_session(agent, recipient_user) is None
            and has_other_contact_channel(agent, recipient_user)
        ):
            return _web_unavailable_result(
                current_requester=is_current_requester,
                will_continue=will_continue,
            )

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
