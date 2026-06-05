import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.exceptions import InvalidTag

from api.encryption import SecretsEncryption
from api.models import (
    LLMProvider,
    LLMRoutingProfile,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    RealtimeVoiceModelEndpoint,
)
from console.agent_chat.timeline import build_processing_snapshot, serialize_plan_snapshot


REALTIME_COMPANION_INSTRUCTIONS = """
You are the low-latency voice companion for Gobii agent chat.
Speak briefly and naturally. Acknowledge what the human says, report concise
status updates, and tell the human when the durable agent needs input.
You are not the main orchestrator agent. Completed human speech turns are
transcribed and passed to the main durable orchestrator agent, which performs
tasks and runs tools. When the human asks you to do something, briefly confirm
that you will pass it to the main agent, but do not claim the task is done.
Do not claim that you performed durable work yourself. Do not send messages,
emails, contact approvals, payments, or any other side-effecting action.
The durable Gobii agent loop receives transcribed user turns and owns all tool use.
""".strip()
VOICE_CONTEXT_HEADER = """
Use the context below to understand the current agent, conversation, and recent work.
Treat it as read-only context. Do not reveal raw internal IDs unless the human asks
for a specific diagnostic detail. If the context is insufficient, say so briefly.
""".strip()
REALTIME_PROVIDER_KEY = "azure_openai_realtime"
REALTIME_ENDPOINT_KEY = "azure_openai_realtime_default"
DEFAULT_REALTIME_VOICE = "marin"
DEFAULT_REALTIME_TRANSCRIPTION_MODEL = "gpt-4o-transcribe-latest"
DEFAULT_REALTIME_TIMEOUT_SECONDS = 10.0
MAX_LOGGED_PROVIDER_ERROR_CHARS = 1000
RECENT_VOICE_MESSAGE_LIMIT = 12
RECENT_VOICE_TOOL_CALL_LIMIT = 8
MAX_CONTEXT_FIELD_CHARS = 1400
MAX_TOOL_RESULT_CHARS = 700
MAX_VOICE_CONTEXT_CHARS = 12000

logger = logging.getLogger(__name__)


class VoiceRealtimeConfigurationError(ValueError):
    pass


class VoiceRealtimeProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceRealtimeSession:
    client_secret: str
    calls_url: str
    expires_at: Any
    voice: str
    deployment: str
    transcription_model: str


@dataclass(frozen=True)
class VoiceRealtimeConfig:
    enabled: bool
    endpoint: str
    deployment: str
    voice: str
    transcription_model: str
    api_key: str
    api_key_configured: bool
    provider_id: str | None = None
    endpoint_id: str | None = None


def get_realtime_voice_config() -> VoiceRealtimeConfig:
    endpoint = _get_realtime_endpoint()
    provider = endpoint.provider if endpoint and endpoint.provider_id else _get_realtime_provider()
    api_key = _resolve_realtime_provider_api_key(provider)
    return VoiceRealtimeConfig(
        enabled=bool(endpoint and endpoint.enabled),
        endpoint=(endpoint.api_base or "").strip() if endpoint else "",
        deployment=(endpoint.deployment or "").strip() if endpoint else "",
        voice=((endpoint.voice or "").strip() if endpoint else "") or DEFAULT_REALTIME_VOICE,
        transcription_model=((endpoint.transcription_model or "").strip() if endpoint else "") or DEFAULT_REALTIME_TRANSCRIPTION_MODEL,
        api_key=api_key,
        api_key_configured=bool(api_key),
        provider_id=str(provider.id) if provider else None,
        endpoint_id=str(endpoint.id) if endpoint else None,
    )


def build_realtime_calls_url(endpoint: str) -> str:
    root = _normalize_azure_realtime_endpoint(endpoint)
    return f"{root}/openai/v1/realtime/calls?webrtcfilter=on"


def create_azure_realtime_session(
    *,
    voice: str | None = None,
    agent: PersistentAgent | None = None,
    user: Any = None,
) -> VoiceRealtimeSession:
    config = get_realtime_voice_config()
    _validate_realtime_settings(config)

    selected_voice = (voice or config.voice).strip()
    if not selected_voice:
        selected_voice = DEFAULT_REALTIME_VOICE

    endpoint_root = _normalize_azure_realtime_endpoint(config.endpoint)
    deployment = config.deployment.strip()
    calls_url = build_realtime_calls_url(config.endpoint)
    request_body = {
        "session": {
            "type": "realtime",
            "model": deployment,
            "instructions": build_realtime_companion_instructions(agent, user=user),
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": "server_vad",
                        "interrupt_response": True,
                    },
                    "transcription": {
                        "model": config.transcription_model,
                    },
                },
                "output": {
                    "voice": selected_voice,
                },
            },
        },
    }

    try:
        response = httpx.post(
            f"{endpoint_root}/openai/v1/realtime/client_secrets",
            headers={
                "api-key": config.api_key,
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=DEFAULT_REALTIME_TIMEOUT_SECONDS,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise VoiceRealtimeProviderError("Azure realtime session request failed.") from exc

    if response.status_code >= 400:
        logger.warning(
            "Azure realtime client secret request rejected: status=%s body=%s",
            response.status_code,
            _sanitize_provider_error_body(response.text, config.api_key),
        )
        raise VoiceRealtimeProviderError("Azure realtime session request was rejected.")

    try:
        data = response.json()
    except ValueError as exc:
        raise VoiceRealtimeProviderError("Azure realtime session response was not valid JSON.") from exc

    client_secret, expires_at = _extract_client_secret(data)
    return VoiceRealtimeSession(
        client_secret=client_secret,
        calls_url=calls_url,
        expires_at=expires_at,
        voice=selected_voice,
        deployment=deployment,
        transcription_model=config.transcription_model,
    )


def build_realtime_companion_instructions(
    agent: PersistentAgent | None = None,
    *,
    user: Any = None,
) -> str:
    sections = [REALTIME_COMPANION_INSTRUCTIONS]
    if agent is not None:
        context = _build_agent_voice_context(agent, user=user)
        if context:
            sections.extend([VOICE_CONTEXT_HEADER, context])
    return "\n\n".join(sections)


def _build_agent_voice_context(agent: PersistentAgent, *, user: Any = None) -> str:
    lines: list[str] = []
    lines.append("## Agent")
    lines.append(f"Name: {_truncate_text(agent.name, 180)}")
    if agent.short_description:
        lines.append(f"Short description: {_truncate_text(agent.short_description, 280)}")
    if agent.charter:
        lines.append("Charter:")
        lines.append(_truncate_text(agent.charter, MAX_CONTEXT_FIELD_CHARS))
    if agent.schedule:
        lines.append(f"Schedule: {_truncate_text(agent.schedule, 180)}")

    human_name = _resolve_user_display_name(user)
    if human_name:
        lines.append("")
        lines.append("## Human")
        lines.append(f"Name: {_truncate_text(human_name, 180)}")

    lines.append("")
    lines.append("## Voice Handoff")
    lines.append(
        "You speak with the human in realtime. The main orchestrator agent reads "
        "the transcribed user turns and performs durable work. For task requests, "
        "acknowledge the request and say you will pass it to the main agent."
    )

    plan_lines = _build_voice_plan_lines(agent)
    if plan_lines:
        lines.append("")
        lines.append("## Current Plan")
        lines.extend(plan_lines)

    processing_lines = _build_voice_processing_lines(agent)
    if processing_lines:
        lines.append("")
        lines.append("## Current Processing")
        lines.extend(processing_lines)

    message_lines = _build_recent_message_lines(agent)
    if message_lines:
        lines.append("")
        lines.append("## Recent Messages")
        lines.extend(message_lines)

    tool_lines = _build_recent_tool_call_lines(agent)
    if tool_lines:
        lines.append("")
        lines.append("## Recent Tool Calls")
        lines.extend(tool_lines)

    return _truncate_text("\n".join(lines), MAX_VOICE_CONTEXT_CHARS)


def _resolve_user_display_name(user: Any) -> str:
    if user is None:
        return ""
    full_name_getter = getattr(user, "get_full_name", None)
    if callable(full_name_getter):
        full_name = _clean_inline_text(str(full_name_getter() or ""))
        if full_name:
            return full_name
    for attr in ("email", "username"):
        value = _clean_inline_text(str(getattr(user, attr, "") or ""))
        if value:
            return value
    return ""


def _build_voice_plan_lines(agent: PersistentAgent) -> list[str]:
    snapshot = serialize_plan_snapshot(agent)
    lines: list[str] = []
    counts = (
        f"todo={snapshot.get('todoCount', 0)}, "
        f"doing={snapshot.get('doingCount', 0)}, "
        f"done={snapshot.get('doneCount', 0)}"
    )
    lines.append(f"Counts: {counts}")
    for label, key in (("Doing", "doingTitles"), ("Todo", "todoTitles"), ("Done", "doneTitles")):
        titles = [_truncate_text(str(title), 160) for title in snapshot.get(key, [])[:5]]
        if titles:
            lines.append(f"{label}: " + "; ".join(titles))
    files = snapshot.get("files") or []
    if files:
        labels = [
            _truncate_text(str(item.get("label") or item.get("path") or "File"), 120)
            for item in files[:5]
            if isinstance(item, dict)
        ]
        if labels:
            lines.append("Deliverable files: " + "; ".join(labels))
    messages = snapshot.get("messages") or []
    if messages:
        labels = [
            _truncate_text(str(item.get("label") or "Message"), 120)
            for item in messages[:5]
            if isinstance(item, dict)
        ]
        if labels:
            lines.append("Deliverable messages: " + "; ".join(labels))
    return lines


def _build_voice_processing_lines(agent: PersistentAgent) -> list[str]:
    snapshot = build_processing_snapshot(agent)
    lines = [f"Processing active: {'yes' if snapshot.active else 'no'}"]
    for task in snapshot.web_tasks[:5]:
        prompt = _truncate_text(str(task.get("promptPreview") or task.get("prompt") or ""), 220)
        status = str(task.get("statusLabel") or task.get("status") or "active")
        if prompt:
            lines.append(f"Browser task: {status} - {prompt}")
        else:
            lines.append(f"Browser task: {status}")
    if snapshot.next_scheduled_at:
        lines.append(f"Next scheduled run: {snapshot.next_scheduled_at.isoformat()}")
    return lines


def _build_recent_message_lines(agent: PersistentAgent) -> list[str]:
    messages = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("from_endpoint", "to_endpoint")
        .order_by("-timestamp", "-seq")[:RECENT_VOICE_MESSAGE_LIMIT]
    )
    lines: list[str] = []
    for message in reversed(list(messages)):
        direction = "agent" if message.is_outbound else "human"
        channel = _message_channel(message)
        body = _truncate_text(_clean_inline_text(message.body), 500)
        if not body:
            continue
        lines.append(f"- {direction} via {channel}: {body}")
    return lines


def _build_recent_tool_call_lines(agent: PersistentAgent) -> list[str]:
    steps = (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False)
        .select_related("tool_call")
        .order_by("-created_at", "-id")[:RECENT_VOICE_TOOL_CALL_LIMIT]
    )
    lines: list[str] = []
    for step in reversed(list(steps)):
        tool_call = step.tool_call
        status = tool_call.status or "complete"
        params = _truncate_text(_clean_inline_text(str(tool_call.tool_params or "")), 320)
        result = _truncate_text(_clean_inline_text(tool_call.result or ""), MAX_TOOL_RESULT_CHARS)
        line = f"- {tool_call.tool_name} [{status}]"
        if params:
            line += f" params={params}"
        if result:
            line += f" result={result}"
        lines.append(line)
    return lines


def _message_channel(message: PersistentAgentMessage) -> str:
    if message.conversation_id and getattr(message, "conversation", None):
        return message.conversation.channel
    endpoint = message.to_endpoint if message.is_outbound else message.from_endpoint
    if endpoint:
        return endpoint.channel
    return "unknown"


def _clean_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _truncate_text(value: str, max_chars: int) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 1].rstrip()}…"


def _validate_realtime_settings(config: VoiceRealtimeConfig) -> None:
    if not config.enabled:
        raise VoiceRealtimeConfigurationError("Voice mode is not enabled.")
    missing = [
        label
        for label, value in (
            ("Azure OpenAI realtime endpoint", config.endpoint),
            ("Azure OpenAI realtime API key", config.api_key),
            ("Azure OpenAI realtime deployment", config.deployment),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise VoiceRealtimeConfigurationError("Voice mode is not configured.")


def _normalize_azure_realtime_endpoint(endpoint: str) -> str:
    root = (endpoint or "").strip().rstrip("/")
    if root.endswith("/openai/v1"):
        root = root[: -len("/openai/v1")]
    if root.endswith("/openai"):
        root = root[: -len("/openai")]
    if not root:
        raise VoiceRealtimeConfigurationError("Voice mode is not configured.")
    return root


def _get_realtime_provider() -> LLMProvider | None:
    return LLMProvider.objects.filter(key=REALTIME_PROVIDER_KEY).first()


def _get_realtime_endpoint() -> RealtimeVoiceModelEndpoint | None:
    profile = (
        LLMRoutingProfile.objects.select_related("voice_endpoint__provider")
        .filter(is_active=True, is_eval_snapshot=False)
        .first()
    )
    if profile and profile.voice_endpoint:
        return profile.voice_endpoint

    enabled_endpoint = (
        RealtimeVoiceModelEndpoint.objects.select_related("provider")
        .filter(enabled=True)
        .order_by("provider__display_name", "deployment")
        .first()
    )
    if enabled_endpoint:
        return enabled_endpoint
    default_endpoint = (
        RealtimeVoiceModelEndpoint.objects.select_related("provider")
        .filter(key=REALTIME_ENDPOINT_KEY)
        .first()
    )
    if default_endpoint:
        return default_endpoint
    return (
        RealtimeVoiceModelEndpoint.objects.select_related("provider")
        .order_by("provider__display_name", "deployment")
        .first()
    )


def _resolve_realtime_provider_api_key(provider: LLMProvider | None) -> str:
    if provider is None or not provider.enabled:
        return ""
    if provider.api_key_encrypted:
        try:
            return SecretsEncryption.decrypt_value(provider.api_key_encrypted) or ""
        except (InvalidTag, TypeError, ValueError):
            return ""
    if provider.env_var_name:
        return (os.getenv(provider.env_var_name) or "").strip()
    return ""


def _extract_client_secret(payload: dict[str, Any]) -> tuple[str, Any]:
    secret_payload = payload.get("client_secret")
    if isinstance(secret_payload, dict):
        value = secret_payload.get("value")
        expires_at = secret_payload.get("expires_at")
    elif isinstance(secret_payload, str):
        value = secret_payload
        expires_at = payload.get("expires_at")
    else:
        value = payload.get("value")
        expires_at = payload.get("expires_at")

    if not isinstance(value, str) or not value:
        raise VoiceRealtimeProviderError("Azure realtime session response did not include a client secret.")
    return value, expires_at


def _sanitize_provider_error_body(body: str, api_key: str) -> str:
    sanitized = (body or "").strip()
    if api_key:
        sanitized = sanitized.replace(api_key, "[redacted]")
    if len(sanitized) > MAX_LOGGED_PROVIDER_ERROR_CHARS:
        return f"{sanitized[:MAX_LOGGED_PROVIDER_ERROR_CHARS]}..."
    return sanitized
