"""Helpers for persistent-agent human input requests."""

from dataclasses import dataclass
import json
import re
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.html import escape
from django.utils.text import slugify

from api.agent.tools.email_sender import execute_send_email
from api.agent.tools.sms_sender import execute_send_sms
from api.models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    build_web_agent_address,
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


@dataclass(slots=True)
class PreparedHumanInputResponse:
    request: PersistentAgentHumanInputRequest
    body: str
    raw_payload: dict[str, Any]
    selected_option_key: str
    selected_option_title: str
    free_text: str


def _coerce_string(value: Any) -> str:
    return str(value or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


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
    include_reference: bool,
) -> str:
    lines = [request_obj.question.strip()]
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
    if include_reference:
        lines.append(f"Ref: {request_obj.reference_code}")
    return "\n".join(line for line in lines if line is not None)


def _render_prompt_html(request_obj: PersistentAgentHumanInputRequest) -> str:
    parts = [
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
        return {
            "status": "ok",
            "delivery": "composer_panel",
        }
    if channel == CommsChannel.SMS:
        return execute_send_sms(
            agent,
            {
                "to_number": target.address,
                "body": _render_prompt_text(request_obj, compact=True, include_reference=True),
                "will_continue_work": False,
            },
        )
    if channel == CommsChannel.EMAIL:
        return execute_send_email(
            agent,
            {
                "to_address": target.address,
                "subject": _truncate(f"Quick question: {request_obj.question}", 120),
                "mobile_first_html": _render_prompt_html(request_obj),
                "will_continue_work": False,
            },
        )
    return {"status": "error", "message": f"Unsupported channel '{channel}' for human input requests."}


def _create_human_input_request_for_target(
    agent: PersistentAgent,
    target: HumanInputTarget,
    *,
    question: str,
    raw_options: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    options = build_option_payloads(raw_options)
    input_mode = (
        PersistentAgentHumanInputRequest.InputMode.OPTIONS_PLUS_TEXT
        if options
        else PersistentAgentHumanInputRequest.InputMode.FREE_TEXT_ONLY
    )
    request_obj = PersistentAgentHumanInputRequest.objects.create(
        agent=agent,
        conversation=target.conversation,
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


def create_human_input_request(
    agent: PersistentAgent,
    *,
    question: str,
    raw_options: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    target = resolve_human_input_target(agent)
    if target is None:
        return {
            "status": "error",
            "message": "No eligible human conversation is available to request input from.",
        }

    return _create_human_input_request_for_target(
        agent,
        target,
        question=question,
        raw_options=raw_options,
    )


def create_human_input_requests_batch(
    agent: PersistentAgent,
    *,
    requests: list[dict[str, Any]],
) -> dict[str, Any]:
    target = resolve_human_input_target(agent)
    if target is None:
        return {
            "status": "error",
            "message": "No eligible human conversation is available to request input from.",
        }

    created_requests: list[dict[str, Any]] = []
    for request in requests:
        result = _create_human_input_request_for_target(
            agent,
            target,
            question=_coerce_string(request.get("question")),
            raw_options=request.get("options"),
        )
        status = _coerce_string(result.get("status")).lower()
        if status not in HUMAN_INPUT_SUCCESS_STATUSES:
            return result
        created_requests.append(result)

    return {
        "status": "ok",
        "message": f"{len(created_requests)} human input requests sent via {target.channel}.",
        "request_ids": [result["request_id"] for result in created_requests if result.get("request_id")],
        "requests": created_requests,
        "requests_count": len(created_requests),
        "active_conversation_channel": target.channel,
        "auto_sleep_ok": True,
    }


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
        "referenceCode": request_obj.reference_code,
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
