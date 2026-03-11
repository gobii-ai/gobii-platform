"""Helpers for persistent-agent human input requests."""

from dataclasses import dataclass
import json
import re
from typing import Any

from django.utils import timezone
from django.utils.html import escape
from django.utils.text import slugify

from api.agent.comms.adapters import ParsedMessage
from api.agent.comms.message_service import ingest_inbound_message
from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.sms_sender import execute_send_sms
from api.agent.tools.web_chat_sender import execute_send_chat_message
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentConversation,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
)

HUMAN_INPUT_SUCCESS_STATUSES = {"ok", "queued", "sent", "success"}
OPTION_NUMBER_RE = re.compile(r"^\s*(?:option\s+)?(?P<number>\d{1,2})(?:[\)\.\:\-\s]|$)", re.IGNORECASE)
REFERENCE_CODE_RE = re.compile(r"\b(HIR-[A-Z0-9]{6})\b", re.IGNORECASE)
MAX_OPTION_COUNT = 6


@dataclass(slots=True)
class HumanInputTarget:
    channel: str
    address: str
    conversation: PersistentAgentConversation


def _coerce_string(value: Any) -> str:
    return str(value or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


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


def _render_prompt_text(request_obj: PersistentAgentHumanInputRequest, *, compact: bool) -> str:
    lines = [request_obj.title.strip(), request_obj.question.strip()]
    options = request_obj.options_json if isinstance(request_obj.options_json, list) else []
    if options:
        lines.append("")
        for index, option in enumerate(options, start=1):
            title = _coerce_string(option.get("title"))
            description = _coerce_string(option.get("description"))
            if compact:
                entry = f"{index}. {title}"
                if description:
                    entry += f" - {_truncate(description, 72)}"
            else:
                entry = f"{index}. {title}"
                if description:
                    entry += f" - {description}"
            lines.append(entry)
        lines.append("")
        lines.append("Reply with the number, the option title, or your own words.")
    else:
        lines.append("")
        lines.append("Reply in your own words.")
    lines.append(f"Ref: {request_obj.reference_code}")
    return "\n".join(line for line in lines if line is not None)


def _render_prompt_html(request_obj: PersistentAgentHumanInputRequest) -> str:
    parts = [
        f"<p><strong>{escape(request_obj.title)}</strong></p>",
        f"<p>{escape(request_obj.question)}</p>",
    ]
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
        parts.append("<p>Reply with the number, the option title, or your own words.</p>")
    else:
        parts.append("<p>Reply in your own words.</p>")
    parts.append(f"<p><strong>Ref:</strong> {escape(request_obj.reference_code)}</p>")
    return "".join(parts)


def _send_request_prompt(
    agent: PersistentAgent,
    request_obj: PersistentAgentHumanInputRequest,
    target: HumanInputTarget,
) -> dict[str, Any]:
    channel = target.channel
    if channel == CommsChannel.WEB:
        return execute_send_chat_message(
            agent,
            {
                "body": _render_prompt_text(request_obj, compact=False),
                "to_address": target.address,
                "will_continue_work": False,
            },
        )
    if channel == CommsChannel.SMS:
        return execute_send_sms(
            agent,
            {
                "to_number": target.address,
                "body": _render_prompt_text(request_obj, compact=True),
                "will_continue_work": False,
            },
        )
    if channel == CommsChannel.EMAIL:
        return execute_send_email(
            agent,
            {
                "to_address": target.address,
                "subject": _truncate(f"Quick question: {request_obj.title}", 120),
                "mobile_first_html": _render_prompt_html(request_obj),
                "will_continue_work": False,
            },
        )
    return {"status": "error", "message": f"Unsupported channel '{channel}' for human input requests."}


def create_human_input_request(
    agent: PersistentAgent,
    *,
    title: str,
    question: str,
    raw_options: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    target = resolve_human_input_target(agent)
    if target is None:
        return {
            "status": "error",
            "message": "No eligible human conversation is available to request input from.",
        }

    options = build_option_payloads(raw_options)
    input_mode = (
        PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT
        if options
        else PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY
    )
    request_obj = PersistentAgentHumanInputRequest.objects.create(
        agent=agent,
        conversation=target.conversation,
        title=title,
        question=question,
        options_json=options,
        input_mode=input_mode,
        requested_via_channel=target.channel,
    )

    send_result = _send_request_prompt(agent, request_obj, target)
    status = str(send_result.get("status") or "").lower()
    if status not in HUMAN_INPUT_SUCCESS_STATUSES:
        request_obj.delete()
        return send_result

    message_id = send_result.get("message_id")
    if message_id:
        message = PersistentAgentMessage.objects.filter(pk=message_id).first()
        if message is not None:
            request_obj.requested_message = message
            request_obj.save(update_fields=["requested_message", "updated_at"])

    return {
        "status": "ok",
        "message": f"Human input request sent via {target.channel}.",
        "request_id": str(request_obj.id),
        "reference_code": request_obj.reference_code,
        "input_mode": request_obj.input_mode,
        "options_count": len(options),
        "active_conversation_channel": target.channel,
        "auto_sleep_ok": True,
    }


def attach_originating_step_from_result(step, result: dict[str, Any] | None) -> None:
    if not step or not isinstance(result, dict):
        return
    request_id = result.get("request_id")
    if not request_id:
        return
    PersistentAgentHumanInputRequest.objects.filter(
        id=request_id,
        originating_step__isnull=True,
    ).update(originating_step=step, updated_at=timezone.now())


def serialize_pending_human_input_request(request_obj: PersistentAgentHumanInputRequest) -> dict[str, Any]:
    return {
        "id": str(request_obj.id),
        "title": request_obj.title,
        "question": request_obj.question,
        "options": request_obj.options_json if isinstance(request_obj.options_json, list) else [],
        "createdAt": request_obj.created_at.isoformat() if request_obj.created_at else None,
        "status": request_obj.status,
        "referenceCode": request_obj.reference_code,
        "activeConversationChannel": request_obj.requested_via_channel,
        "inputMode": request_obj.input_mode,
    }


def list_pending_human_input_requests(agent: PersistentAgent) -> list[dict[str, Any]]:
    requests = (
        PersistentAgentHumanInputRequest.objects.filter(
            agent=agent,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        )
        .order_by("-created_at")
    )
    return [serialize_pending_human_input_request(request_obj) for request_obj in requests]


def serialize_human_input_tool_result(step, raw_result: Any) -> Any:
    """Overlay the latest request state onto the originating tool result."""

    if step is None:
        return raw_result

    prefetched_requests = getattr(step, "_prefetched_objects_cache", {}).get("human_input_requests")
    request_obj = prefetched_requests[0] if prefetched_requests else None
    if request_obj is None:
        request_obj = (
            PersistentAgentHumanInputRequest.objects.filter(originating_step=step)
            .order_by("-created_at")
            .first()
        )
    if request_obj is None:
        return raw_result

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
            "title": request_obj.title,
            "question": request_obj.question,
            "options": request_obj.options_json if isinstance(request_obj.options_json, list) else [],
            "status": request_obj.status,
            "reference_code": request_obj.reference_code,
            "active_conversation_channel": request_obj.requested_via_channel,
            "input_mode": request_obj.input_mode,
            "selected_option_key": request_obj.selected_option_key or None,
            "selected_option_title": request_obj.selected_option_title or None,
            "free_text": request_obj.free_text or None,
            "raw_reply_text": request_obj.raw_reply_text or None,
            "resolution_source": request_obj.resolution_source or None,
        }
    )
    return result_data


def _normalize_text_for_match(value: str) -> str:
    normalized = re.sub(r"\s+", " ", (value or "").strip().lower())
    normalized = re.sub(r"^[\-\*\d\.\)\:\s]+", "", normalized)
    return normalized


def _extract_reference_code(text: str) -> str | None:
    match = REFERENCE_CODE_RE.search(text or "")
    if not match:
        return None
    return match.group(1).upper()


def _strip_reference_code(text: str, reference_code: str | None) -> str:
    if not text:
        return ""
    if not reference_code:
        return text.strip()
    stripped = re.sub(re.escape(reference_code), "", text, flags=re.IGNORECASE)
    stripped = re.sub(r"^[\[\]\(\)\:\-\s]+", "", stripped)
    return stripped.strip()


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


def _resolve_request_candidates(
    message: PersistentAgentMessage,
    direct_request_id: str | None,
    reference_code: str | None,
) -> list[PersistentAgentHumanInputRequest]:
    if direct_request_id:
        direct = PersistentAgentHumanInputRequest.objects.filter(
            id=direct_request_id,
            agent_id=message.owner_agent_id,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        ).first()
        return [direct] if direct else []

    if reference_code:
        referenced = PersistentAgentHumanInputRequest.objects.filter(
            agent_id=message.owner_agent_id,
            reference_code=reference_code,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        ).first()
        return [referenced] if referenced else []

    if not message.conversation_id:
        return []

    return list(
        PersistentAgentHumanInputRequest.objects.filter(
            conversation_id=message.conversation_id,
            status=PersistentAgentHumanInputRequest.Status.PENDING,
        ).order_by("-created_at")
    )


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
    reference_code = _extract_reference_code(body_text)
    cleaned_body = _strip_reference_code(body_text, reference_code)

    candidates = _resolve_request_candidates(message, direct_request_id, reference_code)
    if not candidates:
        return None

    request_obj = candidates[0]

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
        if reference_code:
            resolution_source = PersistentAgentHumanInputRequest.ResolutionSource.REFERENCE_CODE
        if matched_by_number:
            selected_option_key, selected_option_title = matched_by_number
            resolution_source = resolution_source or PersistentAgentHumanInputRequest.ResolutionSource.OPTION_NUMBER
        elif matched_by_title:
            selected_option_key, selected_option_title = matched_by_title
            resolution_source = resolution_source or PersistentAgentHumanInputRequest.ResolutionSource.OPTION_TITLE
        else:
            free_text = cleaned_body
            resolution_source = resolution_source or PersistentAgentHumanInputRequest.ResolutionSource.FREE_TEXT
    else:
        free_text = cleaned_body
        resolution_source = (
            PersistentAgentHumanInputRequest.ResolutionSource.REFERENCE_CODE
            if reference_code
            else PersistentAgentHumanInputRequest.ResolutionSource.FREE_TEXT
        )

    request_obj.selected_option_key = selected_option_key
    request_obj.selected_option_title = selected_option_title
    request_obj.free_text = free_text
    request_obj.raw_reply_text = body_text
    request_obj.raw_reply_message = message
    request_obj.resolution_source = resolution_source
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


def submit_human_input_response(
    request_obj: PersistentAgentHumanInputRequest,
    *,
    selected_option_key: str | None = None,
    free_text: str | None = None,
) -> PersistentAgentMessage:
    body, raw_payload = build_human_input_response_message(
        request_obj,
        selected_option_key=selected_option_key,
        free_text=free_text,
    )

    recipient_address = (
        request_obj.requested_message.from_endpoint.address
        if request_obj.requested_message_id and request_obj.requested_message and request_obj.requested_message.from_endpoint_id
        else ""
    )
    if not recipient_address:
        raise ValueError("Request is missing the agent recipient endpoint.")

    parsed = ParsedMessage(
        sender=request_obj.conversation.address,
        recipient=recipient_address,
        subject=(
            _coerce_string((request_obj.requested_message.raw_payload or {}).get("subject"))
            if request_obj.requested_message_id and isinstance(request_obj.requested_message.raw_payload, dict)
            else None
        ),
        body=body,
        attachments=[],
        raw_payload=raw_payload,
        msg_channel=CommsChannel(request_obj.conversation.channel),
    )
    info = ingest_inbound_message(request_obj.conversation.channel, parsed, filespace_import_mode="sync")
    return info.message
