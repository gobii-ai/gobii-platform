"""Helpers for persistent-agent human input requests."""

import logging
from dataclasses import dataclass
from email.utils import parseaddr
import json
import re
from typing import Any

from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.html import escape
from django.utils.text import slugify

from api.agent.core.llm_config import get_summarization_llm_config
from api.agent.core.llm_utils import run_completion
from api.agent.core.token_usage import log_agent_completion
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    build_web_agent_address,
)

OPTION_NUMBER_RE = re.compile(r"^\s*(?:option\s+)?(?P<number>\d{1,2})(?:[\)\.\:\-\s]|$)", re.IGNORECASE)
BATCH_ANSWER_ENTRY_RE = re.compile(r"^\s*(?P<number>\d{1,2})[\)\.\:\-]\s*(?P<body>.*)$")
MAX_OPTION_COUNT = 6
HUMAN_INPUT_LLM_MAX_CANDIDATES = 20
HUMAN_INPUT_LLM_MATCH_CONFIDENCE_THRESHOLD = 0.8
HUMAN_INPUT_RELAY_MODE_PANEL_ONLY = "panel_only"
HUMAN_INPUT_RELAY_MODE_EXPLICIT_SEND_REQUIRED = "explicit_send_required"

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HumanInputTarget:
    channel: str
    address: str
    conversation: PersistentAgentConversation


@dataclass(slots=True)
class HumanInputRecipient:
    channel: str
    address: str


@dataclass(slots=True)
class PreparedHumanInputResponse:
    request: PersistentAgentHumanInputRequest
    body: str
    raw_payload: dict[str, Any]
    selected_option_key: str
    selected_option_title: str
    free_text: str


@dataclass(slots=True)
class ResolvedHumanInputResponse:
    request: PersistentAgentHumanInputRequest
    selected_option_key: str
    selected_option_title: str
    free_text: str
    resolution_source: str
    raw_reply_text: str


@dataclass(slots=True)
class LLMHumanInputMatch:
    request_id: str
    confidence: float
    answer_span: str


def _coerce_string(value: Any) -> str:
    return str(value or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _build_option_line(option: dict[str, Any], *, index: int, compact: bool) -> str:
    title = _coerce_string(option.get("title"))
    description = _coerce_string(option.get("description"))
    line = f"{index}. {title}"
    if description:
        detail = _truncate(description, 72) if compact else description
        line += f" - {detail}"
    return line


def _build_request_lines(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    compact: bool,
    question_number: int | None = None,
    option_indent: str = "",
) -> list[str]:
    question_prefix = f"{question_number}. " if question_number is not None else ""
    lines = [f"{question_prefix}{request_obj.question.strip()}"]
    options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
    if options:
        for index, option in enumerate(options, start=1):
            lines.append(f"{option_indent}{_build_option_line(option, index=index, compact=compact)}")
        reply_hint = "Reply with the option number, the option title, or your own words."
    else:
        reply_hint = "Reply in your own words."
    lines.append(f"{option_indent}{reply_hint}".rstrip())
    return lines


def _get_or_create_endpoint(
    *,
    channel: str,
    address: str,
    owner_agent: PersistentAgent | None = None,
) -> PersistentAgentCommsEndpoint:
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=address,
    )
    if owner_agent is not None and endpoint.owner_agent_id != owner_agent.id:
        endpoint.owner_agent = owner_agent
        endpoint.save(update_fields=["owner_agent"])
    return endpoint


def _ensure_conversation_participants(
    conversation: PersistentAgentConversation,
    human_endpoint: PersistentAgentCommsEndpoint,
    agent_endpoint: PersistentAgentCommsEndpoint,
) -> None:
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conversation,
        endpoint=human_endpoint,
        defaults={"role": PersistentAgentConversationParticipant.ParticipantRole.HUMAN_USER},
    )
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conversation,
        endpoint=agent_endpoint,
        defaults={"role": PersistentAgentConversationParticipant.ParticipantRole.AGENT},
    )


def build_option_payloads(raw_options: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    if not raw_options:
        return []

    options: list[dict[str, str]] = []
    used_keys: set[str] = set()
    for index, raw_option in enumerate(raw_options[:MAX_OPTION_COUNT], start=1):
        title = _coerce_string(raw_option.get("title"))
        description = _coerce_string(raw_option.get("description"))
        base_key = slugify(title).replace("-", "_") if title else ""
        candidate = base_key or f"option_{index}"
        suffix = 2
        while candidate in used_keys:
            candidate = f"{base_key or f'option_{index}'}_{suffix}"
            suffix += 1
        used_keys.add(candidate)
        options.append(
            {
                "key": candidate,
                "title": title,
                "description": description,
            }
        )
    return options


def _latest_inbound_human_message(agent: PersistentAgent) -> PersistentAgentMessage | None:
    return (
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=False,
            conversation__isnull=False,
        )
        .exclude(conversation__is_peer_dm=True)
        .select_related("conversation", "from_endpoint")
        .order_by("-timestamp")
        .first()
    )


def _normalize_human_input_recipient(
    raw_recipient: HumanInputRecipient | dict[str, Any] | None,
) -> tuple[HumanInputRecipient | None, dict[str, Any] | None]:
    if raw_recipient is None:
        return None, None

    if isinstance(raw_recipient, HumanInputRecipient):
        channel = raw_recipient.channel
        raw_address = raw_recipient.address
    elif isinstance(raw_recipient, dict):
        channel = _coerce_string(raw_recipient.get("channel")).lower()
        raw_address = _coerce_string(raw_recipient.get("address"))
    else:
        return None, {
            "status": "error",
            "message": "Recipient must be an object with channel and address.",
        }

    if channel not in {CommsChannel.WEB, CommsChannel.EMAIL, CommsChannel.SMS}:
        return None, {
            "status": "error",
            "message": "Recipient channel must be one of: web, email, sms.",
        }

    normalized_address = PersistentAgentCommsEndpoint.normalize_address(channel, raw_address)
    if not normalized_address:
        return None, {
            "status": "error",
            "message": "Recipient address is required when recipient is provided.",
        }

    return HumanInputRecipient(channel=channel, address=normalized_address), None


def _get_or_create_human_input_conversation(
    agent: PersistentAgent,
    *,
    channel: str,
    address: str,
) -> PersistentAgentConversation:
    conversation = (
        PersistentAgentConversation.objects.filter(
            owner_agent=agent,
            channel=channel,
            address=address,
        )
        .order_by("id")
        .first()
    )
    if conversation is not None:
        return conversation

    unowned_conversation = (
        PersistentAgentConversation.objects.filter(
            owner_agent__isnull=True,
            channel=channel,
            address=address,
        )
        .order_by("id")
        .first()
    )
    if unowned_conversation is not None:
        unowned_conversation.owner_agent = agent
        unowned_conversation.save(update_fields=["owner_agent"])
        return unowned_conversation

    return PersistentAgentConversation.objects.create(
        owner_agent=agent,
        channel=channel,
        address=address,
    )


def _resolve_explicit_human_input_target(
    agent: PersistentAgent,
    recipient: HumanInputRecipient,
) -> tuple[HumanInputTarget | None, dict[str, Any] | None]:
    if not agent.is_recipient_whitelisted(recipient.channel, recipient.address):
        return None, {
            "status": "error",
            "message": f"Recipient {recipient.address} is not eligible for {recipient.channel} delivery.",
        }

    conversation = _get_or_create_human_input_conversation(
        agent,
        channel=recipient.channel,
        address=recipient.address,
    )
    return HumanInputTarget(
        channel=recipient.channel,
        address=recipient.address,
        conversation=conversation,
    ), None


def resolve_human_input_target(agent: PersistentAgent) -> HumanInputTarget | None:
    latest_inbound = _latest_inbound_human_message(agent)
    if latest_inbound and latest_inbound.conversation_id:
        return HumanInputTarget(
            channel=latest_inbound.conversation.channel,
            address=(latest_inbound.from_endpoint.address if latest_inbound.from_endpoint_id else latest_inbound.conversation.address),
            conversation=latest_inbound.conversation,
        )

    preferred = getattr(agent, "preferred_contact_endpoint", None)
    if preferred and preferred.channel == CommsChannel.WEB:
        conversation = agent.owned_conversations.filter(
            channel=CommsChannel.WEB,
            address=preferred.address,
        ).first()
        if conversation:
            return HumanInputTarget(
                channel=CommsChannel.WEB,
                address=preferred.address,
                conversation=conversation,
            )

    latest_web_conversation = agent.owned_conversations.filter(channel=CommsChannel.WEB).order_by("-id").first()
    if latest_web_conversation:
        return HumanInputTarget(
            channel=CommsChannel.WEB,
            address=latest_web_conversation.address,
            conversation=latest_web_conversation,
        )

    return None


def _render_prompt_text(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    compact: bool,
) -> str:
    return "\n".join(_build_request_lines(request_obj, compact=compact))


def _render_prompt_html(request_obj: PersistentAgentHumanInputRequest) -> str:
    parts = [f"<p>{escape(request_obj.question)}</p>"]
    options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
    if options:
        parts.append("<ol>")
        for option in options:
            title = escape(_coerce_string(option.get("title")))
            description = escape(_coerce_string(option.get("description")))
            if description:
                parts.append(f"<li><strong>{title}</strong><br>{description}</li>")
            else:
                parts.append(f"<li><strong>{title}</strong></li>")
        parts.append("</ol>")
        parts.append("<p>Reply with the option number, the option title, or your own words.</p>")
    else:
        parts.append("<p>Reply in your own words.</p>")
    return "".join(parts)


def _render_batch_prompt_text(
    request_objects: list[PersistentAgentHumanInputRequest],
    *,
    compact: bool,
) -> str:
    lines = [
        "Please answer each question below.",
        "Reply with one answer per line using the matching question number, for example:",
        "1. <your answer>",
        "2. <your answer>",
    ]
    for index, request_obj in enumerate(request_objects, start=1):
        lines.append("")
        lines.extend(
            _build_request_lines(
                request_obj,
                compact=compact,
                question_number=index,
                option_indent="   ",
            )
        )
    return "\n".join(lines)


def _render_batch_prompt_html(request_objects: list[PersistentAgentHumanInputRequest]) -> str:
    parts = [
        "<p>Please answer each question below.</p>",
        "<p>Reply with one answer per line using the matching question number, for example:<br>1. &lt;your answer&gt;<br>2. &lt;your answer&gt;</p>",
        "<ol>",
    ]
    for request_obj in request_objects:
        parts.append(f"<li><p>{escape(request_obj.question)}</p>")
        options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
        if options:
            parts.append("<ol>")
            for option in options:
                title = escape(_coerce_string(option.get("title")))
                description = escape(_coerce_string(option.get("description")))
                if description:
                    parts.append(f"<li><strong>{title}</strong><br>{description}</li>")
                else:
                    parts.append(f"<li><strong>{title}</strong></li>")
            parts.append("</ol>")
            parts.append("<p>Reply with the option number, the option title, or your own words.</p>")
        else:
            parts.append("<p>Reply in your own words.</p>")
        parts.append("</li>")
    parts.append("</ol>")
    return "".join(parts)


def _build_email_subject(request_objects: list[PersistentAgentHumanInputRequest]) -> str:
    if len(request_objects) == 1:
        return _truncate(f"Quick question: {request_objects[0].question}", 120)

    first_question = _coerce_string(request_objects[0].question)
    remaining_count = max(0, len(request_objects) - 1)
    suffix = f" (+{remaining_count} more)" if remaining_count else ""
    return _truncate(f"Quick questions: {first_question}{suffix}", 120)


def _build_relay_payload(
    request_objects: list[PersistentAgentHumanInputRequest],
    target: HumanInputTarget,
) -> tuple[str, dict[str, Any]]:
    ordered_requests = _order_requests_for_batch(request_objects)
    if target.channel == CommsChannel.WEB:
        return HUMAN_INPUT_RELAY_MODE_PANEL_ONLY, {
            "kind": "panel",
            "ui_surface": "human_input_composer_panel",
            "message": "This request is already visible in the web chat composer panel.",
        }

    if target.channel == CommsChannel.EMAIL:
        body_text = (
            _render_prompt_text(ordered_requests[0], compact=False)
            if len(ordered_requests) == 1
            else _render_batch_prompt_text(ordered_requests, compact=False)
        )
        mobile_first_html = (
            _render_prompt_html(ordered_requests[0])
            if len(ordered_requests) == 1
            else _render_batch_prompt_html(ordered_requests)
        )
        return HUMAN_INPUT_RELAY_MODE_EXPLICIT_SEND_REQUIRED, {
            "kind": "send_email",
            "tool_name": "send_email",
            "to_address": target.address,
            "subject": _build_email_subject(ordered_requests),
            "mobile_first_html": mobile_first_html,
            "body_text": body_text,
        }

    if target.channel == CommsChannel.SMS:
        body = (
            _render_prompt_text(ordered_requests[0], compact=True)
            if len(ordered_requests) == 1
            else _render_batch_prompt_text(ordered_requests, compact=True)
        )
        return HUMAN_INPUT_RELAY_MODE_EXPLICIT_SEND_REQUIRED, {
            "kind": "send_sms",
            "tool_name": "send_sms",
            "to_number": target.address,
            "body": body,
        }

    raise ValueError(f"Unsupported channel '{target.channel}' for human input requests.")


def _build_request_result(
    request_objects: list[PersistentAgentHumanInputRequest],
    target: HumanInputTarget,
    *,
    status: str = "ok",
    message: str | None = None,
    partial_success: bool = False,
) -> dict[str, Any]:
    ordered_requests = _order_requests_for_batch(request_objects)
    relay_mode, relay_payload = _build_relay_payload(ordered_requests, target)
    request_ids = [str(request_obj.id) for request_obj in ordered_requests]
    request_count = len(request_ids)

    if message is None:
        if relay_mode == HUMAN_INPUT_RELAY_MODE_PANEL_ONLY:
            message = (
                "Created 1 human input request. It is visible in the web chat composer panel."
                if request_count == 1
                else f"Created {request_count} human input requests. They are visible in the web chat composer panel."
            )
        else:
            tool_name = relay_payload.get("tool_name") or "send tool"
            message = (
                f"Created 1 human input request for {target.channel}. Use {tool_name} with relay_payload to deliver it."
                if request_count == 1
                else (
                    f"Created {request_count} human input requests for {target.channel}. "
                    f"Use {tool_name} with relay_payload to deliver them."
                )
            )

    result = {
        "status": status,
        "message": message,
        "request_id": request_ids[0] if request_ids else None,
        "request_ids": request_ids,
        "requests_count": request_count,
        "target_channel": target.channel,
        "target_address": target.address,
        "relay_mode": relay_mode,
        "relay_payload": relay_payload,
    }
    if partial_success:
        result["partial_success"] = True
    if status == "ok" and relay_mode == HUMAN_INPUT_RELAY_MODE_PANEL_ONLY:
        result["auto_sleep_ok"] = True
    return result


def _create_human_input_request_for_target(
    agent: PersistentAgent,
    target: HumanInputTarget,
    *,
    question: str,
    raw_options: list[dict[str, Any]] | None,
    recipient: HumanInputRecipient | None = None,
) -> tuple[PersistentAgentHumanInputRequest | None, dict[str, Any] | None]:
    options = build_option_payloads(raw_options)
    input_mode = (
        PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT
        if options
        else PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY
    )
    try:
        request_obj = PersistentAgentHumanInputRequest.objects.create(
            agent=agent,
            conversation=target.conversation,
            question=question,
            options_json=options,
            input_mode=input_mode,
            recipient_channel=recipient.channel if recipient else "",
            recipient_address=recipient.address if recipient else "",
            requested_via_channel=target.channel,
        )
    except DatabaseError as exc:
        logger.exception(
            "Failed creating human input request for agent %s on channel %s",
            agent.id,
            target.channel,
        )
        return None, {
            "status": "error",
            "message": f"Failed to create human input request: {exc}",
        }

    return request_obj, None


def create_human_input_request(
    agent: PersistentAgent,
    *,
    question: str,
    raw_options: list[dict[str, Any]] | None,
    recipient: HumanInputRecipient | dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_recipient, error = _normalize_human_input_recipient(recipient)
    if error:
        return error

    if normalized_recipient is not None:
        target, target_error = _resolve_explicit_human_input_target(agent, normalized_recipient)
        if target_error:
            return target_error
    else:
        target = resolve_human_input_target(agent)
        if target is None:
            return {
                "status": "error",
                "message": "No eligible human conversation is available to request input from.",
            }

    request_obj, create_error = _create_human_input_request_for_target(
        agent,
        target,
        question=question,
        raw_options=raw_options,
        recipient=normalized_recipient,
    )
    if create_error:
        return create_error
    if request_obj is None:
        return {
            "status": "error",
            "message": "Failed to create human input request.",
        }
    return _build_request_result([request_obj], target)


def create_human_input_requests_batch(
    agent: PersistentAgent,
    *,
    requests: list[dict[str, Any]],
    recipient: HumanInputRecipient | dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_recipient, error = _normalize_human_input_recipient(recipient)
    if error:
        return error

    if normalized_recipient is not None:
        target, target_error = _resolve_explicit_human_input_target(agent, normalized_recipient)
        if target_error:
            return target_error
    else:
        target = resolve_human_input_target(agent)
        if target is None:
            return {
                "status": "error",
                "message": "No eligible human conversation is available to request input from.",
            }

    created_requests: list[PersistentAgentHumanInputRequest] = []
    for request in requests:
        request_obj, create_error = _create_human_input_request_for_target(
            agent,
            target,
            question=_coerce_string(request.get("question")),
            raw_options=request.get("options"),
            recipient=normalized_recipient,
        )
        if create_error:
            if created_requests:
                failure_message = _coerce_string(create_error.get("message")) or "A later request failed to be created."
                return _build_request_result(
                    created_requests,
                    target,
                    status="error",
                    message=(
                        f"Created {len(created_requests)} of {len(requests)} human input requests before a later request failed. "
                        f"{failure_message}"
                    ),
                    partial_success=True,
                )
            return create_error
        if request_obj is None:
            if created_requests:
                return _build_request_result(
                    created_requests,
                    target,
                    status="error",
                    message=(
                        f"Created {len(created_requests)} of {len(requests)} human input requests before a later request failed."
                    ),
                    partial_success=True,
                )
            return {
                "status": "error",
                "message": "Failed to create human input request batch.",
            }
        created_requests.append(request_obj)

    return _build_request_result(created_requests, target)


def attach_originating_step_from_result(step, result: dict[str, Any] | None) -> None:
    if not step or not isinstance(result, dict):
        return
    request_ids: list[str] = []
    request_id = result.get("request_id")
    if request_id:
        request_ids.append(str(request_id))
    raw_request_ids = result.get("request_ids")
    if isinstance(raw_request_ids, list):
        request_ids.extend(str(value) for value in raw_request_ids if value)
    if not request_ids:
        return
    PersistentAgentHumanInputRequest.objects.filter(
        id__in=request_ids,
        originating_step__isnull=True,
    ).update(originating_step=step, updated_at=timezone.now())


def serialize_pending_human_input_request(request_obj: PersistentAgentHumanInputRequest) -> dict[str, Any]:
    return {
        "id": str(request_obj.id),
        "question": request_obj.question,
        "options": request_obj.options_json if isinstance(request_obj.options_json, list) else [],
        "createdAt": request_obj.created_at.isoformat() if request_obj.created_at else None,
        "status": request_obj.status,
        "activeConversationChannel": request_obj.requested_via_channel,
        "inputMode": request_obj.input_mode,
    }


def list_pending_human_input_requests(agent: PersistentAgent) -> list[dict[str, Any]]:
    request_objects = list(
        PersistentAgentHumanInputRequest.objects.filter(
            agent=agent,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        )
        .order_by("-created_at")
    )
    ordered_for_batches = sorted(
        request_objects,
        key=lambda request: (
            str(request.originating_step_id or request.id),
            request.created_at or timezone.now(),
            str(request.id),
        ),
    )
    batch_members: dict[str, list[PersistentAgentHumanInputRequest]] = {}
    for request_obj in ordered_for_batches:
        batch_key = str(request_obj.originating_step_id or request_obj.id)
        batch_members.setdefault(batch_key, []).append(request_obj)

    serialized_requests: list[dict[str, Any]] = []
    for request_obj in request_objects:
        batch_key = str(request_obj.originating_step_id or request_obj.id)
        requests_in_batch = batch_members.get(batch_key, [request_obj])
        serialized = serialize_pending_human_input_request(request_obj)
        serialized["batchId"] = batch_key
        serialized["batchPosition"] = requests_in_batch.index(request_obj) + 1
        serialized["batchSize"] = len(requests_in_batch)
        serialized_requests.append(serialized)
    return serialized_requests


def serialize_human_input_tool_result(step, raw_result: Any) -> Any:
    """Overlay the latest request state onto the originating tool result."""

    if step is None:
        return raw_result

    prefetched_requests = getattr(step, "_prefetched_objects_cache", {}).get("human_input_requests")
    request_objects = list(prefetched_requests) if prefetched_requests is not None else []
    if not request_objects:
        request_objects = list(
            PersistentAgentHumanInputRequest.objects.filter(originating_step=step).order_by("-created_at")
        )
    if not request_objects:
        return raw_result
    request_obj = request_objects[0]

    if isinstance(raw_result, dict):
        result_data = dict(raw_result)
    elif isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
        except (TypeError, ValueError):
            parsed = None
        result_data = parsed if isinstance(parsed, dict) else {}
    else:
        result_data = {}

    result_data.update(
        {
            "request_id": str(request_obj.id),
            "question": request_obj.question,
            "options": request_obj.options_json if isinstance(request_obj.options_json, list) else [],
            "status": request_obj.status,
            "active_conversation_channel": request_obj.requested_via_channel,
            "input_mode": request_obj.input_mode,
            "selected_option_key": request_obj.selected_option_key or None,
            "selected_option_title": request_obj.selected_option_title or None,
            "free_text": request_obj.free_text or None,
            "raw_reply_text": request_obj.raw_reply_text or None,
            "resolution_source": request_obj.resolution_source or None,
        }
    )
    if len(request_objects) > 1:
        result_data["request_ids"] = [str(request.id) for request in request_objects]
        result_data["requests_count"] = len(request_objects)
        result_data["requests"] = [
            {
                "request_id": str(request.id),
                "question": request.question,
                "options": request.options_json if isinstance(request.options_json, list) else [],
                "status": request.status,
                "input_mode": request.input_mode,
                "selected_option_key": request.selected_option_key or None,
                "selected_option_title": request.selected_option_title or None,
                "free_text": request.free_text or None,
                "raw_reply_text": request.raw_reply_text or None,
                "resolution_source": request.resolution_source or None,
            }
            for request in request_objects
        ]
    return result_data


def _normalize_text_for_match(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().lower())
    normalized = re.sub(r"^[\-\*\d\.\)\:\s]+", "", normalized)
    return normalized


def _match_option_by_number(
    request_obj: PersistentAgentHumanInputRequest,
    body_text: str,
) -> tuple[str, str] | None:
    options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
    if not options:
        return None
    match = OPTION_NUMBER_RE.match(body_text or "")
    if not match:
        return None
    index = int(match.group("number")) - 1
    if index < 0 or index >= len(options):
        return None
    option = options[index]
    return _coerce_string(option.get("key")), _coerce_string(option.get("title"))


def _match_option_by_title(
    request_obj: PersistentAgentHumanInputRequest,
    body_text: str,
) -> tuple[str, str] | None:
    normalized_body = _normalize_text_for_match(body_text)
    if not normalized_body:
        return None
    options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
    for option in options:
        title = _coerce_string(option.get("title"))
        normalized_title = _normalize_text_for_match(title)
        if not normalized_title:
            continue
        if normalized_body == normalized_title:
            return _coerce_string(option.get("key")), title
        if normalized_body.startswith(normalized_title):
            return _coerce_string(option.get("key")), title
    return None


def _batch_key_for_request(request_obj: PersistentAgentHumanInputRequest) -> str:
    return str(request_obj.originating_step_id or request_obj.id)


def _order_requests_for_batch(
    requests: list[PersistentAgentHumanInputRequest],
) -> list[PersistentAgentHumanInputRequest]:
    return sorted(
        requests,
        key=lambda request: (
            request.created_at or timezone.now(),
            str(request.id),
        ),
    )


def _get_pending_batch_for_request(
    request_obj: PersistentAgentHumanInputRequest,
) -> list[PersistentAgentHumanInputRequest]:
    queryset = PersistentAgentHumanInputRequest.objects.filter(
        agent_id=request_obj.agent_id,
        status=PersistentAgentHumanInputRequest.Status.PENDING,
    )
    if request_obj.originating_step_id:
        queryset = queryset.filter(originating_step_id=request_obj.originating_step_id)
    else:
        queryset = queryset.filter(id=request_obj.id)
    return _order_requests_for_batch(list(queryset))


def _request_has_explicit_recipient(
    request_obj: PersistentAgentHumanInputRequest,
) -> bool:
    return bool(
        _coerce_string(request_obj.recipient_channel)
        and _coerce_string(request_obj.recipient_address)
    )

def _get_message_sender_address(message: PersistentAgentMessage) -> str:
    raw_address = ""
    if message.from_endpoint_id:
        raw_address = message.from_endpoint.address
    elif message.conversation_id:
        raw_address = message.conversation.address
    return PersistentAgentCommsEndpoint.normalize_address(
        message.conversation.channel,
        raw_address,
    ) or ""


def _sender_is_authorized_for_request(
    request_obj: PersistentAgentHumanInputRequest,
    message: PersistentAgentMessage,
) -> bool:
    sender_address = _get_message_sender_address(message)
    if not sender_address:
        return False

    if _request_has_explicit_recipient(request_obj):
        return (
            message.conversation.channel == request_obj.recipient_channel
            and sender_address == request_obj.recipient_address
        )

    return request_obj.agent.is_internal_responder_identity(
        message.conversation.channel,
        sender_address,
    )


def _get_authorized_pending_requests_for_message(
    message: PersistentAgentMessage,
) -> list[PersistentAgentHumanInputRequest]:
    if not message.owner_agent_id:
        return []

    request_objects = (
        PersistentAgentHumanInputRequest.objects.select_related("agent", "conversation")
        .filter(
            agent_id=message.owner_agent_id,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        )
        .order_by("-created_at")
    )

    authorized_requests: list[PersistentAgentHumanInputRequest] = []
    for request_obj in request_objects:
        if _sender_is_authorized_for_request(request_obj, message):
            authorized_requests.append(request_obj)
        if len(authorized_requests) >= HUMAN_INPUT_LLM_MAX_CANDIDATES:
            break
    return authorized_requests


def _get_authorized_pending_requests_for_conversation(
    message: PersistentAgentMessage,
) -> list[PersistentAgentHumanInputRequest]:
    if not message.conversation_id:
        return []

    request_objects = (
        PersistentAgentHumanInputRequest.objects.select_related("agent", "conversation")
        .filter(
            conversation_id=message.conversation_id,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        )
        .order_by("-created_at")
    )
    return [
        request_obj
        for request_obj in request_objects
        if _sender_is_authorized_for_request(request_obj, message)
    ]


def _get_unambiguous_authorized_batch_for_message(
    message: PersistentAgentMessage,
) -> list[PersistentAgentHumanInputRequest]:
    request_objects = _get_authorized_pending_requests_for_message(message)
    if not request_objects:
        return []

    ordered_requests = _order_requests_for_batch(request_objects)
    batch_members: dict[str, list[PersistentAgentHumanInputRequest]] = {}
    for request_obj in ordered_requests:
        batch_members.setdefault(_batch_key_for_request(request_obj), []).append(request_obj)
    if len(batch_members) != 1:
        return []
    return next(iter(batch_members.values()))


def _extract_numbered_batch_answers(text: str) -> list[tuple[int, str]]:
    numbered_answers: list[tuple[int, str]] = []
    current_number: int | None = None
    current_lines: list[str] = []

    for raw_line in (text or "").splitlines():
        match = BATCH_ANSWER_ENTRY_RE.match(raw_line)
        if match:
            if current_number is not None:
                answer_body = "\n".join(current_lines).strip()
                if answer_body:
                    numbered_answers.append((current_number, answer_body))
            current_number = int(match.group("number"))
            current_lines = [_coerce_string(match.group("body"))]
            continue
        if current_number is not None:
            current_lines.append(raw_line.rstrip())

    if current_number is not None:
        answer_body = "\n".join(current_lines).strip()
        if answer_body:
            numbered_answers.append((current_number, answer_body))
    return numbered_answers


def _split_paragraph_batch_answers(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"\n\s*\n+", text or "") if chunk.strip()]


def _get_sender_scoped_pending_requests(
    message: PersistentAgentMessage,
) -> list[PersistentAgentHumanInputRequest]:
    return _get_authorized_pending_requests_for_message(message)


def _get_single_batch_requests(
    requests: list[PersistentAgentHumanInputRequest],
) -> list[PersistentAgentHumanInputRequest]:
    if not requests:
        return []
    ordered_requests = _order_requests_for_batch(requests)
    batch_keys = {_batch_key_for_request(request_obj) for request_obj in ordered_requests}
    if len(batch_keys) != 1:
        return []
    return ordered_requests


def _coerce_match_confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_human_input_match_tool_def() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "resolve_human_input_requests",
            "description": "Return the pending request matches extracted from the inbound human reply.",
            "parameters": {
                "type": "object",
                "properties": {
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "request_id": {"type": "string"},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "answer_span": {"type": "string"},
                            },
                            "required": ["request_id", "confidence", "answer_span"],
                        },
                    }
                },
                "required": ["matches"],
            },
        },
    }


def _extract_human_input_match_tool_payload(response: Any) -> dict[str, Any] | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    if message is None and isinstance(choices[0], dict):
        message = choices[0].get("message")
    if message is None:
        return None

    raw_tool_calls = getattr(message, "tool_calls", None)
    if raw_tool_calls is None and isinstance(message, dict):
        raw_tool_calls = message.get("tool_calls")
    tool_calls = raw_tool_calls or []
    if not tool_calls:
        raw_function_call = getattr(message, "function_call", None)
        if raw_function_call is None and isinstance(message, dict):
            raw_function_call = message.get("function_call")
        if raw_function_call:
            tool_calls = [
                {
                    "function": raw_function_call,
                }
            ]

    for tool_call in tool_calls:
        function_block = getattr(tool_call, "function", None) or (
            tool_call.get("function") if isinstance(tool_call, dict) else None
        )
        if not function_block:
            continue
        function_name = getattr(function_block, "name", None) or (
            function_block.get("name") if isinstance(function_block, dict) else None
        )
        if function_name != "resolve_human_input_requests":
            continue
        raw_args = getattr(function_block, "arguments", None) or (
            function_block.get("arguments") if isinstance(function_block, dict) else None
        ) or "{}"
        if isinstance(raw_args, dict):
            return raw_args
        return json.loads(raw_args)
    return None


def _normalize_answer_span_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _body_contains_explicit_answer(body_text: str, snippet: str) -> bool:
    normalized_body = _normalize_answer_span_key(body_text)
    normalized_snippet = _normalize_answer_span_key(snippet)
    if not normalized_body or not normalized_snippet:
        return False
    return normalized_snippet in normalized_body


def _deserialize_llm_human_input_matches(payload: dict[str, Any]) -> list[LLMHumanInputMatch]:
    if isinstance(payload, list):
        raw_matches = payload
    elif isinstance(payload, dict):
        raw_matches = payload.get("matches")
    else:
        raw_matches = None

    if not isinstance(raw_matches, list):
        raise ValueError("Human input matcher did not return a matches array.")

    normalized_matches: list[LLMHumanInputMatch] = []
    for raw_match in raw_matches:
        if not isinstance(raw_match, dict):
            continue
        normalized_matches.append(
            LLMHumanInputMatch(
                request_id=_coerce_string(raw_match.get("request_id")),
                confidence=_coerce_match_confidence(raw_match.get("confidence")),
                answer_span=_coerce_string(raw_match.get("answer_span")),
            )
        )
    return normalized_matches


def _filter_conflicting_llm_matches(
    matches: list[LLMHumanInputMatch],
) -> list[LLMHumanInputMatch]:
    kept_by_request_id: dict[str, LLMHumanInputMatch] = {}
    discarded_request_ids: set[str] = set()

    grouped_matches: dict[str, list[LLMHumanInputMatch]] = {}
    for match in matches:
        if not match.request_id or match.confidence < HUMAN_INPUT_LLM_MATCH_CONFIDENCE_THRESHOLD:
            continue
        grouped_matches.setdefault(match.request_id, []).append(match)

    for request_id, request_matches in grouped_matches.items():
        request_matches.sort(key=lambda item: item.confidence, reverse=True)
        top_confidence = request_matches[0].confidence
        top_matches = [
            item for item in request_matches if abs(item.confidence - top_confidence) < 1e-9
        ]
        if len(top_matches) > 1:
            discarded_request_ids.add(request_id)
            continue
        kept_by_request_id[request_id] = request_matches[0]

    surviving_matches = [
        match for request_id, match in kept_by_request_id.items() if request_id not in discarded_request_ids
    ]

    conflicted_request_ids: set[str] = set()
    for index, left_match in enumerate(surviving_matches):
        left_span = _normalize_answer_span_key(left_match.answer_span)
        if not left_span:
            continue
        for right_match in surviving_matches[index + 1:]:
            right_span = _normalize_answer_span_key(right_match.answer_span)
            if not right_span:
                continue
            if (
                left_span == right_span
                or left_span in right_span
                or right_span in left_span
            ):
                conflicted_request_ids.add(left_match.request_id)
                conflicted_request_ids.add(right_match.request_id)

    return [
        match for match in surviving_matches if match.request_id not in conflicted_request_ids
    ]


def _build_human_input_matching_payload(
    request_objects: list[PersistentAgentHumanInputRequest],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for request_obj in request_objects:
        batch_requests = _get_pending_batch_for_request(request_obj)
        batch_position = 1
        for index, batch_request in enumerate(batch_requests, start=1):
            if batch_request.id == request_obj.id:
                batch_position = index
                break
        payloads.append(
            {
                "request_id": str(request_obj.id),
                "question": request_obj.question,
                "input_mode": request_obj.input_mode,
                "options": request_obj.options_json if isinstance(request_obj.options_json, list) else [],
                "created_at": request_obj.created_at.isoformat() if request_obj.created_at else None,
                "batch_position": batch_position,
                "batch_size": len(batch_requests),
            }
        )
    return payloads


def _build_human_input_matching_messages(
    *,
    body_text: str,
    request_objects: list[PersistentAgentHumanInputRequest],
) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": (
                "You map an inbound human reply to pending human-input requests. "
                "Always respond by calling the resolve_human_input_requests tool. "
                "Only match answers explicitly present in the inbound message. "
                "If unsure, omit the match. Never invent request IDs, option keys, or answer text."
            ),
        },
        {
            "role": "user",
            "content": (
                "Inbound message:\n"
                f"{body_text}\n\n"
                "Pending requests:\n"
                f"{json.dumps(_build_human_input_matching_payload(request_objects), indent=2)}"
            ),
        },
    ]


def _resolve_requests_with_llm(
    message: PersistentAgentMessage,
    request_objects: list[PersistentAgentHumanInputRequest],
    *,
    body_text: str,
) -> list[ResolvedHumanInputResponse]:
    if len(request_objects) < 2:
        return []
    if not body_text:
        return []

    try:
        provider, model, params = get_summarization_llm_config(agent=message.owner_agent)
        completion_params = dict(params)
        completion_params.setdefault("temperature", 0)
        tools = [_build_human_input_match_tool_def()]
        run_kwargs: dict[str, Any] = {}
        if completion_params.get("supports_tool_choice", True):
            run_kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": "resolve_human_input_requests"},
            }
        response = run_completion(
            model=model,
            messages=_build_human_input_matching_messages(
                body_text=body_text,
                request_objects=request_objects,
            ),
            params=completion_params,
            tools=tools,
            drop_params=True,
            **run_kwargs,
        )
        log_agent_completion(
            message.owner_agent,
            completion_type=PersistentAgentCompletion.CompletionType.OTHER,
            response=response,
            model=model,
            provider=provider,
        )
        tool_payload = _extract_human_input_match_tool_payload(response)
        if not tool_payload:
            logger.info(
                "Human input LLM matcher returned no resolution tool call for message %s",
                getattr(message, "id", None),
            )
            return []
        raw_matches = _deserialize_llm_human_input_matches(tool_payload)
    except Exception as exc:
        logger.exception(
            "Human input LLM matching failed closed for message %s",
            getattr(message, "id", None),
        )
        return []

    filtered_matches = _filter_conflicting_llm_matches(raw_matches)
    requests_by_id = {str(request_obj.id): request_obj for request_obj in request_objects}
    resolved_requests: list[ResolvedHumanInputResponse] = []

    for match in filtered_matches:
        request_obj = requests_by_id.get(match.request_id)
        if request_obj is None:
            continue

        if not _body_contains_explicit_answer(body_text, match.answer_span):
            continue

        resolved = _resolve_request_response(
            request_obj,
            body_text=match.answer_span,
        )
        resolved.resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.LLM_EXTRACTION
        resolved.raw_reply_text = match.answer_span
        resolved_requests.append(resolved)

    return resolved_requests


def _resolve_requests_with_safe_fallback(
    request_objects: list[PersistentAgentHumanInputRequest],
    *,
    body_text: str,
    allow_single_fallback: bool,
) -> list[ResolvedHumanInputResponse]:
    if not request_objects:
        return []

    if len(request_objects) == 1 and allow_single_fallback:
        return [
            _resolve_request_response(
                request_objects[0],
                body_text=body_text,
            )
        ]

    single_batch = _get_single_batch_requests(request_objects)
    if not single_batch:
        return []

    batch_resolutions = _resolve_batch_requests_from_body(
        single_batch,
        body_text=body_text,
    )
    if batch_resolutions:
        return batch_resolutions

    return []


def _resolve_request_response(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    body_text: str,
    direct_option_key: str = "",
    direct_option_title: str = "",
    direct_request_id: str | None = None,
) -> ResolvedHumanInputResponse:
    cleaned_body = _coerce_string(body_text)
    selected_option_key = ""
    selected_option_title = ""
    free_text = ""
    resolution_source = ""

    if direct_request_id and direct_option_key:
        selected_option_key = direct_option_key
        selected_option_title = direct_option_title
        resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.DIRECT
    elif request_obj.input_mode == PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT:
        matched_by_number = _match_option_by_number(request_obj, cleaned_body)
        matched_by_title = _match_option_by_title(request_obj, cleaned_body)
        if matched_by_number:
            selected_option_key, selected_option_title = matched_by_number
            resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.OPTION_NUMBER
        elif matched_by_title:
            selected_option_key, selected_option_title = matched_by_title
            resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.OPTION_TITLE
        else:
            free_text = cleaned_body
            resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.FREE_TEXT
    else:
        free_text = cleaned_body
        resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.FREE_TEXT

    return ResolvedHumanInputResponse(
        request=request_obj,
        selected_option_key=selected_option_key,
        selected_option_title=selected_option_title,
        free_text=free_text,
        resolution_source=resolution_source,
        raw_reply_text=body_text,
    )


def _apply_resolved_request(
    resolved: ResolvedHumanInputResponse,
    *,
    message: PersistentAgentMessage,
) -> PersistentAgentHumanInputRequest:
    request_obj = resolved.request
    request_obj.selected_option_key = resolved.selected_option_key
    request_obj.selected_option_title = resolved.selected_option_title
    request_obj.free_text = resolved.free_text
    request_obj.raw_reply_text = resolved.raw_reply_text
    request_obj.raw_reply_message = message
    request_obj.resolution_source = resolved.resolution_source
    request_obj.resolved_at = timezone.now()
    request_obj.status = PersistentAgentHumanInputRequest.Status.ANSWERED
    request_obj.save(
        update_fields=[
            "selected_option_key",
            "selected_option_title",
            "free_text",
            "raw_reply_text",
            "raw_reply_message",
            "resolution_source",
            "resolved_at",
            "status",
            "updated_at",
        ]
    )
    return request_obj


def _resolve_batch_requests_from_body(
    requests: list[PersistentAgentHumanInputRequest],
    *,
    body_text: str,
) -> list[ResolvedHumanInputResponse]:
    ordered_requests = _order_requests_for_batch(requests)
    numbered_answers = _extract_numbered_batch_answers(body_text)
    if numbered_answers:
        resolved_by_request_id: dict[str, ResolvedHumanInputResponse] = {}
        for question_number, answer_body in numbered_answers:
            if question_number < 1 or question_number > len(ordered_requests):
                continue
            target_request = ordered_requests[question_number - 1]
            resolved_by_request_id[str(target_request.id)] = _resolve_request_response(
                target_request,
                body_text=answer_body,
            )
        if resolved_by_request_id:
            return [
                resolved_by_request_id[str(request.id)]
                for request in ordered_requests
                if str(request.id) in resolved_by_request_id
            ]

    paragraph_answers = _split_paragraph_batch_answers(body_text)
    if len(paragraph_answers) == len(ordered_requests) and len(paragraph_answers) > 1:
        return [
            _resolve_request_response(
                request_obj,
                body_text=answer_body,
            )
            for request_obj, answer_body in zip(ordered_requests, paragraph_answers)
        ]

    return []


def _get_authorized_pending_request_by_id(
    message: PersistentAgentMessage,
    request_id: str,
) -> PersistentAgentHumanInputRequest | None:
    request_obj = (
        PersistentAgentHumanInputRequest.objects.select_related("agent", "conversation")
        .filter(
            id=request_id,
            agent_id=message.owner_agent_id,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        )
        .first()
    )
    if request_obj is None or not _sender_is_authorized_for_request(request_obj, message):
        return None
    return request_obj


def resolve_human_input_request_for_message(
    message: PersistentAgentMessage,
) -> PersistentAgentHumanInputRequest | None:
    if not message or message.is_outbound or not message.owner_agent_id:
        return None

    raw_payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    direct_request_id = _coerce_string(raw_payload.get("human_input_request_id")) or None
    direct_option_key = _coerce_string(raw_payload.get("human_input_selected_option_key"))
    direct_option_title = _coerce_string(raw_payload.get("human_input_selected_option_title"))
    body_text = _coerce_string(message.body)
    resolved_requests: list[ResolvedHumanInputResponse] = []

    if direct_request_id:
        direct_request = _get_authorized_pending_request_by_id(message, direct_request_id)
        if direct_request is None:
            return None
        resolved_requests = [
            _resolve_request_response(
                direct_request,
                body_text=body_text,
                direct_option_key=direct_option_key,
                direct_option_title=direct_option_title,
                direct_request_id=direct_request_id,
            )
        ]
    else:
        sender_scoped_candidates = _get_sender_scoped_pending_requests(message)
        resolved_requests = _resolve_requests_with_llm(
            message,
            sender_scoped_candidates,
            body_text=body_text,
        )
        if not resolved_requests:
            same_conversation_candidates = _get_authorized_pending_requests_for_conversation(message)
            resolved_requests = _resolve_requests_with_safe_fallback(
                same_conversation_candidates,
                body_text=body_text,
                allow_single_fallback=True,
            )
        if not resolved_requests:
            resolved_requests = _resolve_requests_with_safe_fallback(
                sender_scoped_candidates,
                body_text=body_text,
                allow_single_fallback=True,
            )

    if not resolved_requests:
        return None

    persisted_requests = [
        _apply_resolved_request(
            resolved,
            message=message,
        )
        for resolved in resolved_requests
    ]
    return persisted_requests[0]


def build_human_input_response_message(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    selected_option_key: str | None = None,
    free_text: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if selected_option_key:
        options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
        for option in options:
            if _coerce_string(option.get("key")) == selected_option_key:
                title = _coerce_string(option.get("title"))
                return title, {
                    "human_input_request_id": str(request_obj.id),
                    "human_input_selected_option_key": selected_option_key,
                    "human_input_selected_option_title": title,
                    "source": "console_human_input_response",
                }
        raise ValueError("Selected option key is not valid for this request.")

    body = _coerce_string(free_text)
    if not body:
        raise ValueError("Free text response is required.")
    return body, {
        "human_input_request_id": str(request_obj.id),
        "source": "console_human_input_response",
    }


def _prepare_human_input_response(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    selected_option_key: str | None = None,
    free_text: str | None = None,
) -> PreparedHumanInputResponse:
    body, raw_payload = build_human_input_response_message(
        request_obj,
        selected_option_key=selected_option_key,
        free_text=free_text,
    )
    if selected_option_key:
        options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
        for option in options:
            if _coerce_string(option.get("key")) == selected_option_key:
                return PreparedHumanInputResponse(
                    request=request_obj,
                    body=body,
                    raw_payload=raw_payload,
                    selected_option_key=selected_option_key,
                    selected_option_title=_coerce_string(option.get("title")),
                    free_text="",
                )
        raise ValueError("Selected option key is not valid for this request.")

    clean_text = _coerce_string(free_text)
    if not clean_text:
        raise ValueError("Free text response is required.")
    return PreparedHumanInputResponse(
        request=request_obj,
        body=body,
        raw_payload=raw_payload,
        selected_option_key="",
        selected_option_title="",
        free_text=clean_text,
    )


def _resolve_agent_recipient_address(request_obj: PersistentAgentHumanInputRequest) -> str:
    recipient_address = (
        request_obj.requested_message.from_endpoint.address
        if request_obj.requested_message_id and request_obj.requested_message and request_obj.requested_message.from_endpoint_id
        else ""
    )
    if not recipient_address and request_obj.conversation.channel == CommsChannel.WEB:
        return build_web_agent_address(request_obj.agent_id)
    if recipient_address:
        return recipient_address
    return _coerce_string(
        PersistentAgentCommsEndpoint.objects.filter(
            owner_agent_id=request_obj.agent_id,
            channel=request_obj.conversation.channel,
        )
        .order_by("-is_primary", "id")
        .values_list("address", flat=True)
        .first()
    )


def _build_batch_response_body(prepared_responses: list[PreparedHumanInputResponse]) -> str:
    lines: list[str] = []
    for index, prepared in enumerate(prepared_responses, start=1):
        lines.append(f"Question: {prepared.request.question}")
        lines.append(f"Answer: {prepared.body}")
        if index < len(prepared_responses):
            lines.append("")
    return "\n".join(lines)


def submit_human_input_responses_batch(
    agent: PersistentAgent,
    responses: list[dict[str, str]],
) -> PersistentAgentMessage:
    if not responses:
        raise ValueError("At least one human input response is required.")

    request_ids = [str(response.get("request_id") or "").strip() for response in responses]
    if any(not request_id for request_id in request_ids):
        raise ValueError("Each response must include request_id.")

    request_objects = list(
        PersistentAgentHumanInputRequest.objects.select_related(
            "agent",
            "conversation",
            "requested_message__from_endpoint",
        ).filter(
            id__in=request_ids,
            agent=agent,
        )
    )
    requests_by_id = {str(request.id): request for request in request_objects}
    if len(requests_by_id) != len(request_ids):
        raise ValueError("One or more human input requests could not be found.")

    prepared_responses: list[PreparedHumanInputResponse] = []
    for response in responses:
        request_id = str(response.get("request_id") or "").strip()
        request_obj = requests_by_id[request_id]
        if request_obj.status != PersistentAgentHumanInputRequest.Status.PENDING:
            raise ValueError("This request is no longer pending.")
        prepared_responses.append(
            _prepare_human_input_response(
                request_obj,
                selected_option_key=_coerce_string(response.get("selected_option_key")) or None,
                free_text=_coerce_string(response.get("free_text")) or None,
            )
        )

    first_request = prepared_responses[0].request
    if any(
        prepared.request.conversation_id != first_request.conversation_id
        or prepared.request.requested_via_channel != first_request.requested_via_channel
        for prepared in prepared_responses[1:]
    ):
        raise ValueError("Batch responses must belong to the same conversation and channel.")

    recipient_address = _resolve_agent_recipient_address(first_request)
    if not recipient_address:
        raise ValueError("Request is missing the agent recipient endpoint.")

    body = (
        prepared_responses[0].body
        if len(prepared_responses) == 1
        else _build_batch_response_body(prepared_responses)
    )
    raw_payload: dict[str, Any] = {
        "source": "console_human_input_response_batch" if len(prepared_responses) > 1 else "console_human_input_response",
        "human_input_request_ids": [str(prepared.request.id) for prepared in prepared_responses],
        "human_input_responses": [
            {
                "request_id": str(prepared.request.id),
                "selected_option_key": prepared.selected_option_key or None,
                "selected_option_title": prepared.selected_option_title or None,
                "free_text": prepared.free_text or None,
            }
            for prepared in prepared_responses
        ],
    }
    if len(prepared_responses) == 1:
        raw_payload.update(prepared_responses[0].raw_payload)

    with transaction.atomic():
        human_endpoint = _get_or_create_endpoint(
            channel=first_request.conversation.channel,
            address=first_request.conversation.address,
        )
        agent_endpoint = _get_or_create_endpoint(
            channel=first_request.conversation.channel,
            address=recipient_address,
            owner_agent=agent,
        )
        _ensure_conversation_participants(first_request.conversation, human_endpoint, agent_endpoint)

        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=human_endpoint,
            to_endpoint=agent_endpoint,
            conversation=first_request.conversation,
            owner_agent=agent,
            body=body,
            raw_payload=raw_payload,
        )

        resolved_at = timezone.now()
        for prepared in prepared_responses:
            request_obj = prepared.request
            request_obj.selected_option_key = prepared.selected_option_key
            request_obj.selected_option_title = prepared.selected_option_title
            request_obj.free_text = prepared.free_text
            request_obj.raw_reply_text = prepared.body
            request_obj.raw_reply_message = message
            request_obj.resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.DIRECT
            request_obj.resolved_at = resolved_at
            request_obj.status = PersistentAgentHumanInputRequest.Status.ANSWERED
            request_obj.save(
                update_fields=[
                    "selected_option_key",
                    "selected_option_title",
                    "free_text",
                    "raw_reply_text",
                    "raw_reply_message",
                    "resolution_source",
                    "resolved_at",
                    "status",
                    "updated_at",
                ]
            )

        transaction.on_commit(
            lambda: __import__("api.agent.tasks", fromlist=["process_agent_events_task"])
            .process_agent_events_task.delay(str(agent.id))
        )

    return message


def submit_human_input_response(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    selected_option_key: str | None = None,
    free_text: str | None = None,
) -> PersistentAgentMessage:
    return submit_human_input_responses_batch(
        request_obj.agent,
        [
            {
                "request_id": str(request_obj.id),
                "selected_option_key": selected_option_key or "",
                "free_text": free_text or "",
            }
        ],
    )
