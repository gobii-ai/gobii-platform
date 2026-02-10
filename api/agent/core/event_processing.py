"""
Event processing entry‑point for persistent agents.

This module provides the core logic for processing agent events, including
incoming messages, cron triggers, and other events. It handles the main agent
loop with LLM‑powered reasoning and tool execution using tiered failover.
"""
from __future__ import annotations

import json
import os
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import List, Tuple, Union, Optional, Dict, Any, Literal
from uuid import UUID

import litellm
from opentelemetry import baggage, trace
from pottery import Redlock
from pottery.exceptions import ExtendUnlockedLock, TooManyExtensions
from django.apps import apps
from django.db import DatabaseError, transaction, close_old_connections
from django.db.utils import OperationalError
from django.utils import timezone as dj_timezone
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from observability import mark_span_failed_with_exception
from .budget import (
    AgentBudgetManager,
    BudgetContext,
    get_current_context as get_budget_context,
    set_current_context as set_budget_context,
)
from .burn_control import (
    BURN_RATE_COOLDOWN_SECONDS,
    BURN_RATE_USER_INACTIVITY_MINUTES,
    burn_cooldown_key,
    burn_follow_up_key,
    has_recent_user_message,
    should_pause_for_burn_rate,
)
from .processing_flags import (
    claim_pending_drain_slot,
    clear_processing_heartbeat,
    clear_processing_queued_flag,
    enqueue_pending_agent,
    get_pending_drain_settings,
    is_agent_pending,
    set_processing_heartbeat,
)
from .llm_utils import (
    raise_if_empty_litellm_response,
    raise_if_invalid_litellm_response,
    run_completion,
)
from .llm_streaming import StreamAccumulator
from .token_usage import (
    coerce_int as _coerce_int,
    completion_kwargs_from_usage,
    extract_reasoning_content,
    extract_token_usage,
    set_usage_span_attributes,
    usage_attribute as _usage_attribute,
)
from ..short_description import (
    maybe_schedule_mini_description,
    maybe_schedule_short_description,
)
from ..avatar import maybe_schedule_agent_avatar
from ..tags import maybe_schedule_agent_tags
from tasks.services import TaskCreditService
from util.tool_costs import (
    get_tool_credit_cost,
    get_default_task_credit_cost,
)
from util.constants.task_constants import TASKS_UNLIMITED
from .llm_config import (
    apply_tier_credit_multiplier,
    get_llm_config_with_failover,
    LLMNotConfiguredError,
    is_llm_bootstrap_required,
)
from api.agent.events import publish_agent_event, AgentEventType
from api.agent.comms.message_service import send_owner_daily_credit_hard_limit_notice
from api.evals.execution import get_current_eval_routing_profile
from .prompt_context import (
    INTERNAL_REASONING_PREFIX,
    build_prompt_context,
    get_agent_daily_credit_state,
    get_agent_tools,
)

from ..tools.email_sender import execute_send_email
from ..tools.sms_sender import execute_send_sms
from ..tools.spawn_web_task import execute_spawn_web_task
from ..tools.schedule_updater import execute_update_schedule
from ..tools.charter_updater import execute_update_charter
from ..tools.database_enabler import execute_enable_database
from ..tools.sqlite_agent_config import (
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
)
from ..tools.sqlite_kanban import apply_sqlite_kanban_updates, seed_sqlite_kanban
from console.agent_chat.signals import broadcast_kanban_changes
from ..tools.sqlite_state import agent_sqlite_db
from ..tools.secure_credentials_request import execute_secure_credentials_request
from ..tools.request_contact_permission import execute_request_contact_permission
from ..tools.search_tools import execute_search_tools
from ..tools.tool_manager import execute_enabled_tool, auto_enable_heuristic_tools, should_skip_auto_substitution
from ..tools.web_chat_sender import execute_send_chat_message
from ..tools.peer_dm import execute_send_agent_message
from ..tools.webhook_sender import execute_send_webhook_event
from ..tools.agent_variables import clear_variables, substitute_variables
from ...models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentCompletion,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    PersistentAgentPromptArchive,
    CommsChannel,
    build_web_user_address,
)
from api.services.tool_settings import get_tool_settings_for_owner
from api.services.web_sessions import get_active_web_sessions, has_active_web_session
from config import settings
from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from .gemini_cache import (
    GEMINI_CACHE_BLOCKLIST,
    GeminiCachedContentManager,
    disable_gemini_cache_for,
    is_gemini_cache_conflict_error,
)
from .web_streaming import WebStreamBroadcaster, resolve_web_stream_target

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

MAX_AGENT_LOOP_ITERATIONS = 100
MAX_NO_TOOL_STREAK = 1  # Stop on first no-tool response unless continuation signal present
MAX_ITERATIONS_FOLLOWUP_DELAY_SECONDS = 60
ARG_LOG_MAX_CHARS = 500
RESULT_LOG_MAX_CHARS = 500
AUTO_SLEEP_FLAG = "auto_sleep_ok"
TOOL_ERROR_MESSAGE_MAX_BYTES = 800
TOOL_ERROR_DETAIL_MAX_BYTES = 1500
TOOL_ERROR_TYPE_MAX_BYTES = 120
PREFERRED_PROVIDER_MAX_AGE = timedelta(hours=1)
MESSAGE_TOOL_NAMES = {
    "send_email",
    "send_sms",
    "send_chat_message",
    "send_agent_message",
}
MESSAGE_SUCCESS_STATUSES = {"ok", "queued", "sent", "success"}
MESSAGE_TOOL_BODY_KEYS = {
    "send_email": "mobile_first_html",
    "send_sms": "body",
    "send_chat_message": "body",
    "send_agent_message": "message",
}
# Canonical phrase the agent should use to signal continuation.
# Prompts tell the agent to include this exact phrase when it has more work.
CANONICAL_CONTINUATION_PHRASE = "CONTINUE_WORK_SIGNAL"

# Flexible detection: canonical phrase + natural language variations.
# Case-insensitive matching against message text or thinking content.
CONTINUATION_PHRASES = (
    CANONICAL_CONTINUATION_PHRASE.lower(),  # Canonical - exact match
    "continuing with",
    "let me ",
    "i'll ",
    "i will ",
    "i'm going to ",
    "next i ",
    "now i ",
    "working on ",
    "proceeding to ",
    "moving on to ",
)


def _truncate_text_bytes(text: str, max_bytes: int) -> str:
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _coerce_error_text(value: Any, max_bytes: int) -> str:
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:
        text = "<unprintable>"
    return _truncate_text_bytes(text, max_bytes)


def _is_error_status(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    return isinstance(status, str) and status.lower() == "error"


def _is_warning_status(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    return isinstance(status, str) and status.lower() == "warning"


def _infer_retryable_from_text(message: str) -> bool:
    if not message:
        return False
    lower = message.lower()
    return any(
        token in lower
        for token in (
            "timeout",
            "timed out",
            "temporary",
            "temporarily",
            "rate limit",
            "too many requests",
            "connection reset",
            "connection aborted",
            "connection refused",
            "service unavailable",
            "gateway timeout",
        )
    )


def _build_safe_error_payload(
    message: Any,
    *,
    error_type: Any = None,
    retryable: Optional[bool] = None,
    detail: Any = None,
    status_code: Any = None,
) -> dict:
    safe_message = _coerce_error_text(message or "Tool execution failed.", TOOL_ERROR_MESSAGE_MAX_BYTES)
    payload = {"status": "error", "message": safe_message}
    if error_type:
        payload["error_type"] = _coerce_error_text(error_type, TOOL_ERROR_TYPE_MAX_BYTES)
    if retryable is not None:
        payload["retryable"] = bool(retryable)
    if status_code is not None:
        if isinstance(status_code, int):
            payload["status_code"] = status_code
        elif isinstance(status_code, str):
            payload["status_code"] = _coerce_error_text(status_code, 40)
        else:
            payload["status_code"] = _coerce_error_text(status_code, 40)
    if detail:
        payload["detail"] = _coerce_error_text(detail, TOOL_ERROR_DETAIL_MAX_BYTES)
    return payload


def _normalize_error_result(result: dict) -> dict:
    message = result.get("message") or result.get("error") or result.get("detail") or "Tool returned an error."
    error_type = result.get("error_type") or result.get("type")
    retryable = result.get("retryable") if isinstance(result.get("retryable"), bool) else None
    status_code = result.get("status_code")
    if status_code is None:
        status_code = result.get("code")
    if status_code is None:
        status_code = result.get("error_code")

    detail = None
    for key in ("detail", "error_detail", "traceback", "stacktrace", "exception", "exception.stacktrace"):
        if key in result:
            detail = result.get(key)
            break
    if detail is None:
        exception_block = result.get("exception")
        if isinstance(exception_block, dict):
            detail = (
                exception_block.get("stacktrace")
                or exception_block.get("traceback")
                or exception_block.get("message")
            )

    safe_message = _coerce_error_text(message, TOOL_ERROR_MESSAGE_MAX_BYTES)
    if retryable is None:
        retryable = _infer_retryable_from_text(safe_message)

    return _build_safe_error_payload(
        safe_message,
        error_type=error_type,
        retryable=retryable,
        detail=detail,
        status_code=status_code,
    )



def _has_continuation_signal(text: str) -> bool:
    """Return True if text contains phrases indicating the agent wants to continue."""
    if not text:
        return False
    lower_text = text.lower()
    return any(phrase in lower_text for phrase in CONTINUATION_PHRASES)


def _has_open_kanban_work(agent: PersistentAgent) -> bool:
    """Return True when kanban still has todo/doing work for the agent."""
    KanbanCard = apps.get_model("api", "PersistentAgentKanbanCard")
    return KanbanCard.objects.filter(
        assigned_agent=agent,
        status__in=("todo", "doing"),
    ).exists()


def _remove_canonical_continuation_phrase(text: str) -> tuple[str, bool]:
    if not text:
        return text, False
    phrase = CANONICAL_CONTINUATION_PHRASE
    lower_text = text.lower()
    lower_phrase = phrase.lower()
    if lower_phrase not in lower_text:
        return text, False
    result: list[str] = []
    start = 0
    found = False
    while True:
        idx = lower_text.find(lower_phrase, start)
        if idx == -1:
            result.append(text[start:])
            break
        found = True
        result.append(text[start:idx])
        start = idx + len(phrase)
    return "".join(result), found


def _strip_canonical_continuation_phrase(text: str) -> tuple[str, bool]:
    cleaned, found = _remove_canonical_continuation_phrase(text)
    if found:
        cleaned = cleaned.strip()
    return cleaned, found


def _normalize_tool_result_content(raw: str) -> str:
    """Decode stringified JSON payloads so nested arrays/objects stay structured."""
    from api.agent.tools.json_utils import decode_embedded_json_strings

    if not raw or not isinstance(raw, str):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(parsed, (dict, list)):
        return raw
    normalized = decode_embedded_json_strings(parsed)
    try:
        return json.dumps(normalized, ensure_ascii=False)
    except TypeError:
        return raw


def _should_imply_continue(
    *,
    has_canonical_continuation: bool,
    has_other_tool_calls: bool,
    has_explicit_sleep: bool,
    has_open_kanban_work: bool = False,
    has_natural_continuation_signal: bool = False,
) -> bool:
    if has_explicit_sleep:
        return False
    if has_canonical_continuation or has_other_tool_calls:
        return True
    # Safety valve: if the model language clearly indicates ongoing work and
    # kanban still has open cards, keep the loop alive even without the
    # canonical continuation token.
    return has_open_kanban_work and has_natural_continuation_signal


class _CanonicalContinuationStreamFilter:
    def __init__(self) -> None:
        self._buffer = ""
        self._phrase = CANONICAL_CONTINUATION_PHRASE
        self._lower_phrase = self._phrase.lower()
        self._phrase_len = len(self._phrase)

    def ingest(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        self._buffer += text
        cleaned, _ = _remove_canonical_continuation_phrase(self._buffer)
        self._buffer = cleaned
        tail_len = self._suffix_prefix_len()
        if len(self._buffer) <= tail_len:
            return None
        if tail_len > 0:
            emit = self._buffer[:-tail_len]
            self._buffer = self._buffer[-tail_len:]
        else:
            emit = self._buffer
            self._buffer = ""
        return emit or None

    def flush(self) -> Optional[str]:
        if not self._buffer:
            return None
        cleaned, _ = _remove_canonical_continuation_phrase(self._buffer)
        self._buffer = ""
        cleaned = cleaned.rstrip()
        return cleaned or None

    def _suffix_prefix_len(self) -> int:
        if not self._buffer or self._phrase_len <= 1:
            return 0
        max_len = min(len(self._buffer), self._phrase_len - 1)
        if max_len <= 0:
            return 0
        buffer_lower = self._buffer.lower()
        for i in range(max_len, 0, -1):
            if buffer_lower.endswith(self._lower_phrase[:i]):
                return i
        return 0


# Canonical phrase the agent should use to signal completion (work is done).
# Prompts tell the agent to include this exact phrase when delivering final output.
CANONICAL_COMPLETION_PHRASE = "Work complete."

# Flexible detection: canonical phrase + natural language variations.
# Case-insensitive matching against message text or thinking content.
COMPLETION_PHRASES = (
    "work complete",  # Canonical - exact match (without period for flexibility)
    "task complete",
    "all done",
    "that's everything",
    "that completes",
    "this completes",
    "here are your results",
    "here's what i found",
)

# Explicit message-tool sends without will_continue_work can still be safely
# inferred as "continue" when the message is a clear progress update. These
# phrases indicate the opposite: acknowledge-and-stop / wait-for-user intent.
STOP_HINT_PHRASES = (
    "let me know if you need",
    "if you need anything else",
    "if needed",
    "reach out later",
    "reach out if",
    "don't follow up",
    "do not follow up",
    "won't follow up",
    "i won't follow up",
    "i will not follow up",
    "i'll wait",
    "i will wait",
    "standing by",
    "whenever you're ready",
)


def _has_completion_signal(text: str) -> bool:
    """Return True if text contains phrases indicating the agent is done."""
    if not text:
        return False
    lower_text = text.lower()
    return any(phrase in lower_text for phrase in COMPLETION_PHRASES)


def _has_stop_hint_signal(text: str) -> bool:
    """Return True if text suggests defer/wait intent rather than continued work."""
    if not text:
        return False
    lower_text = text.lower()
    return any(phrase in lower_text for phrase in STOP_HINT_PHRASES)


def _should_infer_message_tool_continuation(message_text: str) -> bool:
    """Infer continuation for explicit message tools when flag is omitted.

    This is intentionally conservative:
    - Continue only on strong continuation language.
    - Never continue on completion/defer hints.
    - Never continue when asking the user a question (usually waiting on input).
    """
    if not message_text:
        return False
    if "?" in message_text:
        return False
    if _has_completion_signal(message_text):
        return False
    if _has_stop_hint_signal(message_text):
        return False
    return _has_continuation_signal(message_text)


__all__ = ["process_agent_events", "CANONICAL_CONTINUATION_PHRASE", "CANONICAL_COMPLETION_PHRASE"]


@dataclass(frozen=True)
class _EventProcessingLockSettings:
    lock_timeout_seconds: int
    lock_extend_interval_seconds: int
    lock_acquire_timeout_seconds: float
    lock_max_extensions: int
    heartbeat_ttl_seconds: int
    pending_set_ttl_seconds: int
    pending_drain_delay_seconds: int
    pending_drain_schedule_ttl_seconds: int


def _get_event_processing_lock_settings() -> _EventProcessingLockSettings:
    lock_timeout_seconds = int(
        getattr(settings, "AGENT_EVENT_PROCESSING_LOCK_TIMEOUT_SECONDS", 900)
    )
    lock_timeout_seconds = max(1, lock_timeout_seconds)
    lock_extend_interval_seconds = int(
        getattr(
            settings,
            "AGENT_EVENT_PROCESSING_LOCK_EXTEND_INTERVAL_SECONDS",
            max(30, lock_timeout_seconds // 2),
        )
    )
    lock_extend_interval_seconds = max(1, lock_extend_interval_seconds)
    lock_acquire_timeout_seconds = float(
        getattr(settings, "AGENT_EVENT_PROCESSING_LOCK_ACQUIRE_TIMEOUT_SECONDS", 1)
    )
    lock_acquire_timeout_seconds = max(0.1, lock_acquire_timeout_seconds)
    lock_max_extensions = int(
        getattr(settings, "AGENT_EVENT_PROCESSING_LOCK_MAX_EXTENSIONS", 200)
    )
    lock_max_extensions = max(1, lock_max_extensions)
    heartbeat_ttl_seconds = int(
        getattr(settings, "AGENT_EVENT_PROCESSING_HEARTBEAT_TTL_SECONDS", lock_timeout_seconds)
    )
    heartbeat_ttl_seconds = max(0, heartbeat_ttl_seconds)
    pending_settings = get_pending_drain_settings(settings)
    return _EventProcessingLockSettings(
        lock_timeout_seconds=lock_timeout_seconds,
        lock_extend_interval_seconds=lock_extend_interval_seconds,
        lock_acquire_timeout_seconds=lock_acquire_timeout_seconds,
        lock_max_extensions=lock_max_extensions,
        heartbeat_ttl_seconds=heartbeat_ttl_seconds,
        pending_set_ttl_seconds=pending_settings.pending_set_ttl_seconds,
        pending_drain_delay_seconds=pending_settings.pending_drain_delay_seconds,
        pending_drain_schedule_ttl_seconds=pending_settings.pending_drain_schedule_ttl_seconds,
    )


@dataclass
class _ProcessingHeartbeat:
    agent_id: str
    ttl_seconds: int
    started_at: float
    redis_client: Any | None = None
    run_id: str | None = None
    worker_pid: int | None = None

    def touch(self, stage: str) -> None:
        if self.ttl_seconds <= 0:
            return
        set_processing_heartbeat(
            self.agent_id,
            ttl=self.ttl_seconds,
            run_id=self.run_id,
            worker_pid=self.worker_pid,
            stage=stage,
            started_at=self.started_at,
            client=self.redis_client,
        )

    def update_run_id(self, run_id: str) -> None:
        self.run_id = run_id
        self.touch("run_started")

    def clear(self) -> None:
        clear_processing_heartbeat(self.agent_id, client=self.redis_client)


class _LockExtender:
    def __init__(self, lock: Redlock, *, interval_seconds: int, span=None) -> None:
        self._lock = lock
        self._interval_seconds = max(1, interval_seconds)
        self._next_extend_at = time.monotonic() + self._interval_seconds
        self._disabled = False
        self._span = span

    def maybe_extend(self) -> None:
        if self._disabled:
            return
        now = time.monotonic()
        if now < self._next_extend_at:
            return
        try:
            self._lock.extend()
            self._next_extend_at = now + self._interval_seconds
            if self._span:
                self._span.add_event("Distributed lock extended")
        except (ExtendUnlockedLock, TooManyExtensions) as exc:
            self._disabled = True
            logger.warning("Lock extension disabled: %s", exc)
            if self._span:
                self._span.add_event("Distributed lock extension disabled")
        except Exception as exc:
            logger.warning("Failed to extend lock: %s", exc)


def _schedule_pending_drain(*, delay_seconds: int, schedule_ttl_seconds: int, span=None) -> None:
    if not claim_pending_drain_slot(ttl=schedule_ttl_seconds):
        return
    try:
        from ..tasks.process_events import process_pending_agent_events_task  # noqa: WPS433 (runtime import)

        process_pending_agent_events_task.apply_async(countdown=delay_seconds)
        if span is not None:
            span.add_event("Pending drain task scheduled")
    except Exception as exc:
        logger.error("Failed to schedule pending drain task: %s", exc)


def _stale_lock_threshold_seconds(
    lock_timeout_seconds: int,
    pending_set_ttl_seconds: int,
) -> int:
    threshold = min(lock_timeout_seconds * 4, pending_set_ttl_seconds)
    return max(1, threshold)


def _lock_storage_keys(lock_key: str) -> tuple[str, ...]:
    prefix = f"{getattr(Redlock, '_KEY_PREFIX', 'redlock')}:"
    if lock_key.startswith(prefix):
        return (lock_key,)
    return (f"{prefix}{lock_key}", lock_key)


def _maybe_clear_stale_lock(
    *,
    lock_key: str,
    lock_timeout_seconds: int,
    pending_set_ttl_seconds: int,
    redis_client,
    span=None,
) -> bool:
    threshold = _stale_lock_threshold_seconds(lock_timeout_seconds, pending_set_ttl_seconds)
    for storage_key in _lock_storage_keys(lock_key):
        try:
            ttl = redis_client.ttl(storage_key)
        except Exception:
            logger.debug("Failed to check lock TTL for %s", storage_key, exc_info=True)
            continue

        if ttl is None or ttl == -2:
            continue

        if ttl == -1 or ttl > threshold:
            try:
                redis_client.delete(storage_key)
                logger.warning(
                    "Cleared stale agent event-processing lock %s (ttl=%s threshold=%s)",
                    storage_key,
                    ttl,
                    threshold,
                )
                if span is not None:
                    span.add_event("Cleared stale distributed lock")
                return True
            except Exception:
                logger.exception("Failed to clear stale lock %s", storage_key)
    return False


def _normalize_persistent_agent_id(persistent_agent_id: Union[str, UUID]) -> Optional[str]:
    if isinstance(persistent_agent_id, UUID):
        return str(persistent_agent_id)
    try:
        return str(UUID(str(persistent_agent_id)))
    except (TypeError, ValueError, AttributeError):
        return None


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
    return None


def _extract_message_content(message: Any) -> str:
    """Return normalized assistant message content, if any."""
    if message is None:
        return ""

    content = None
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                part_type = part.get("type")
                if isinstance(part_type, str) and part_type.lower() in {"reasoning", "thinking"}:
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    return ""


def _coerce_function_call_tool(function_call: Any) -> Optional[dict]:
    if function_call is None:
        return None
    if isinstance(function_call, dict):
        name = function_call.get("name")
        arguments = function_call.get("arguments")
        call_id = function_call.get("id")
    else:
        name = getattr(function_call, "name", None)
        arguments = getattr(function_call, "arguments", None)
        call_id = getattr(function_call, "id", None)
    return {
        "id": call_id or "function_call",
        "type": "function",
        "function": {
            "name": name or "",
            "arguments": arguments or "",
        },
    }


def _tool_calls_from_content(message: Any) -> list[dict]:
    content = None
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    tool_calls: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if not isinstance(part_type, str):
            continue
        part_type = part_type.lower()
        if part_type not in {"tool_use", "tool_call"}:
            continue
        name = part.get("name") or part.get("tool_name")
        raw_input = part.get("input", part.get("arguments"))
        if raw_input is None:
            raw_input = {}
        if isinstance(raw_input, str):
            arguments = raw_input
        else:
            try:
                arguments = json.dumps(raw_input)
            except Exception:
                arguments = str(raw_input)
        tool_calls.append(
            {
                "id": part.get("id") or part.get("tool_use_id") or f"tool_use_{len(tool_calls)}",
                "type": "function",
                "function": {"name": name or "", "arguments": arguments},
            }
        )
    return tool_calls


def _normalize_tool_calls(message: Any) -> list[Any]:
    if message is None:
        return []
    raw_tool_calls = None
    if isinstance(message, dict):
        raw_tool_calls = message.get("tool_calls")
    else:
        raw_tool_calls = getattr(message, "tool_calls", None)
    if raw_tool_calls:
        if isinstance(raw_tool_calls, str):
            try:
                raw_tool_calls = json.loads(raw_tool_calls)
            except Exception:
                return [raw_tool_calls]
        if isinstance(raw_tool_calls, dict):
            return [raw_tool_calls]
        if isinstance(raw_tool_calls, list):
            return list(raw_tool_calls)
        try:
            return list(raw_tool_calls)
        except TypeError:
            return [raw_tool_calls]

    raw_function_call = None
    if isinstance(message, dict):
        raw_function_call = message.get("function_call")
    else:
        raw_function_call = getattr(message, "function_call", None)
    if raw_function_call:
        coerced = _coerce_function_call_tool(raw_function_call)
        return [coerced] if coerced else []

    return _tool_calls_from_content(message)


def _get_tool_call_name(call: Any) -> Optional[str]:
    if call is None:
        return None
    function = getattr(call, "function", None)
    if function is not None:
        name = getattr(function, "name", None)
        if name:
            return _sanitize_tool_name(name)
    if isinstance(call, dict):
        function = call.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if name:
                return _sanitize_tool_name(name)
        name = call.get("name")
        if name:
            return _sanitize_tool_name(name)
    name = getattr(call, "name", None)
    if name:
        return _sanitize_tool_name(name)
    return None


def _sanitize_tool_name(name: str) -> str:
    """Extract just the function name from a tool call.

    Some models (e.g., GLM-4) may return the function name with arguments
    like 'sqlite_batch(sql="...")' instead of just 'sqlite_batch'.
    This extracts the base name before any opening parenthesis.
    """
    if not name:
        return name
    # Strip the function call syntax if present
    paren_idx = name.find("(")
    if paren_idx > 0:
        return name[:paren_idx].strip()
    return name


def _build_tool_call_description(
    tool_name: str,
    tool_params: Dict[str, Any],
    normalized_result: str | None,
) -> str:
    # Keep descriptions compact; they surface in chat captions.
    safe_tool_name = (tool_name or "")[:256]
    try:
        params_preview = str(tool_params)[:100] if tool_params else ""
        result_preview = (normalized_result or "")[:100]
        return f"Tool call: {safe_tool_name}({params_preview}) -> {result_preview}"
    except Exception:
        return f"Tool call: {safe_tool_name}"


def _emit_tool_call_realtime(step: "PersistentAgentStep", context: str) -> None:
    try:
        from console.agent_chat.signals import emit_tool_call_realtime

        emit_tool_call_realtime(step)
    except Exception:
        logger.debug(
            "Failed to broadcast %s tool call for agent %s step %s",
            context,
            getattr(step, "agent_id", None),
            getattr(step, "id", None),
            exc_info=True,
        )


def _emit_tool_call_audit(step: "PersistentAgentStep", context: str) -> None:
    try:
        from console.agent_chat.signals import emit_tool_call_audit

        emit_tool_call_audit(step)
    except Exception:
        logger.debug(
            "Failed to broadcast %s tool call audit for agent %s step %s",
            context,
            getattr(step, "agent_id", None),
            getattr(step, "id", None),
            exc_info=True,
        )


def _persist_tool_call_step(
    agent: "PersistentAgent",
    tool_name: str,
    tool_params: Dict[str, Any],
    result_content: str,
    execution_duration_ms: Optional[int],
    status: str | None,
    credits_consumed: Any,
    consumed_credit: Any,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> Optional["PersistentAgentStep"]:
    """Persist a tool call step with robust error handling.

    This function handles all database errors gracefully to ensure agent
    processing continues even if step persistence fails. The tool has already
    executed - we're just recording it.

    Returns the created step, or None if persistence failed.
    """
    from api.models import PersistentAgentStep, PersistentAgentToolCall
    normalized_result = _normalize_tool_result_content(result_content)

    # Truncate tool_name as a safety measure (should already be sanitized, but be defensive)
    safe_tool_name = (tool_name or "")[:256]

    # Build a safe description (truncate if needed)
    description = _build_tool_call_description(safe_tool_name, tool_params, normalized_result)

    step_kwargs = {
        "agent": agent,
        "description": description[:500],  # Ensure description fits
        "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
        "task_credit": consumed_credit,
    }

    def _try_create_step() -> Optional[PersistentAgentStep]:
        """Attempt to create the step and tool call record."""
        attach_completion(step_kwargs)
        step = PersistentAgentStep.objects.create(**step_kwargs)
        attach_prompt_archive(step)
        tool_call_status = status or "complete"
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=safe_tool_name,
            tool_params=tool_params,
            result=normalized_result,
            execution_duration_ms=execution_duration_ms,
            status=tool_call_status,
        )
        _emit_tool_call_realtime(step, "realtime")
        return step

    # Try primary path
    try:
        step = _try_create_step()
        logger.info(
            "Agent %s: persisted tool call step_id=%s for %s",
            agent.id,
            getattr(step, "id", None),
            safe_tool_name,
        )
        return step
    except OperationalError:
        # Stale connection - retry once
        close_old_connections()
        try:
            step = _try_create_step()
            logger.info(
                "Agent %s: persisted tool call (retry) step_id=%s for %s",
                agent.id,
                getattr(step, "id", None),
                safe_tool_name,
            )
            return step
        except Exception as retry_exc:
            logger.error(
                "Agent %s: failed to persist tool call for %s after retry: %s",
                agent.id,
                safe_tool_name,
                retry_exc,
            )
            return None
    except DatabaseError as db_exc:
        # Data errors, integrity errors, etc. - log and continue
        logger.error(
            "Agent %s: database error persisting tool call for %s: %s",
            agent.id,
            safe_tool_name,
            db_exc,
        )
        return None
    except Exception as exc:
        # Catch-all for unexpected errors - never crash the agent
        logger.error(
            "Agent %s: unexpected error persisting tool call for %s: %s",
            agent.id,
            safe_tool_name,
            exc,
            exc_info=True,
        )
        return None


def _create_pending_tool_call_step(
    agent: "PersistentAgent",
    tool_name: str,
    tool_params: Dict[str, Any],
    credits_consumed: Any,
    consumed_credit: Any,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> Optional["PersistentAgentStep"]:
    from api.models import PersistentAgentStep, PersistentAgentToolCall

    safe_tool_name = (tool_name or "")[:256]
    step_kwargs = {
        "agent": agent,
        "description": "",
        "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
        "task_credit": consumed_credit,
    }

    try:
        attach_completion(step_kwargs)
        step = PersistentAgentStep.objects.create(**step_kwargs)
        attach_prompt_archive(step)
        PersistentAgentToolCall.objects.create(
            step=step,
            tool_name=safe_tool_name,
            tool_params=tool_params,
            result="",
            execution_duration_ms=None,
            status="pending",
        )
        _emit_tool_call_realtime(step, "pending")
        return step
    except Exception:
        logger.debug(
            "Failed to persist pending tool call for agent %s (%s)",
            agent.id,
            safe_tool_name,
            exc_info=True,
        )
        return None


def _finalize_pending_tool_call_step(
    step: "PersistentAgentStep",
    tool_name: str,
    tool_params: Dict[str, Any],
    result_content: str,
    execution_duration_ms: Optional[int],
    status: str,
) -> None:
    from api.models import PersistentAgentToolCall

    normalized_result = _normalize_tool_result_content(result_content)
    safe_tool_name = (tool_name or "")[:256]
    description = _build_tool_call_description(safe_tool_name, tool_params, normalized_result)

    try:
        step.description = description[:500]
        step.save(update_fields=["description"])
    except Exception:
        logger.debug(
            "Failed to update tool step description for agent %s step %s",
            getattr(step, "agent_id", None),
            getattr(step, "id", None),
            exc_info=True,
        )

    created_tool_call = False
    try:
        tool_call = getattr(step, "tool_call", None)
        if tool_call is None:
            tool_call = PersistentAgentToolCall.objects.create(
                step=step,
                tool_name=safe_tool_name,
                tool_params=tool_params,
                result=normalized_result,
                execution_duration_ms=execution_duration_ms,
                status=status,
            )
            created_tool_call = True
        else:
            tool_call.tool_name = safe_tool_name
            tool_call.tool_params = tool_params
            tool_call.result = normalized_result
            tool_call.execution_duration_ms = execution_duration_ms
            tool_call.status = status
            tool_call.save(update_fields=["tool_name", "tool_params", "result", "execution_duration_ms", "status"])
    except Exception:
        logger.debug(
            "Failed to finalize tool call for agent %s step %s",
            getattr(step, "agent_id", None),
            getattr(step, "id", None),
            exc_info=True,
        )
        return

    _emit_tool_call_realtime(step, "finalized")
    if not created_tool_call:
        _emit_tool_call_audit(step, "finalized")


def _get_tool_call_arguments(call: Any) -> Any:
    if call is None:
        return None
    function = getattr(call, "function", None)
    if function is not None:
        arguments = getattr(function, "arguments", None)
        if arguments is not None:
            return arguments
    if isinstance(call, dict):
        function = call.get("function")
        if isinstance(function, dict) and "arguments" in function:
            return function.get("arguments")
        if "arguments" in call:
            return call.get("arguments")
    arguments = getattr(call, "arguments", None)
    return arguments


def _substitute_variables_in_params(params: Any) -> Any:
    """Recursively substitute $[var] placeholders in tool parameters.

    Handles nested dicts, lists, and string values. Non-string values
    are returned unchanged.
    """
    if isinstance(params, str):
        return substitute_variables(params)
    if isinstance(params, dict):
        return {k: _substitute_variables_in_params(v) for k, v in params.items()}
    if isinstance(params, list):
        return [_substitute_variables_in_params(item) for item in params]
    return params


def _get_latest_active_web_session(agent: PersistentAgent):
    for session in get_active_web_sessions(agent):
        if session.user_id is not None:
            return session
    return None


def _build_implied_send_tool_call(
    agent: PersistentAgent,
    message_text: str,
    *,
    will_continue_work: bool,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Build an implied send tool call based on current context.

    Routes to the appropriate channel:
    - Active web chat session (highest priority)
    - Most recent inbound message sender (email, SMS, web, or peer DM)
    """
    from .prompt_context import _get_implied_send_context

    ctx = _get_implied_send_context(agent)
    if not ctx:
        return None, "Implied send failed: no active recipient context."

    channel = ctx.get("channel")
    to_address = ctx.get("to_address")
    if not has_active_web_session(agent):
        return None, "Implied send failed: no active web session."
    if channel != "web":
        return None, "Implied send failed: active web session required."

    if channel == "web":
        tool_params = {"to_address": to_address, "body": message_text}
        if will_continue_work:
            tool_params["will_continue_work"] = True
        return (
            {
                "id": "implied_send",
                "function": {"name": "send_chat_message", "arguments": json.dumps(tool_params)},
            },
            None,
        )

    elif channel == "sms":
        tool_params = {"to_number": to_address, "body": message_text}
        if will_continue_work:
            tool_params["will_continue_work"] = True
        return (
            {
                "id": "implied_send",
                "function": {"name": "send_sms", "arguments": json.dumps(tool_params)},
            },
            None,
        )

    elif channel == "email":
        # Wrap plain text in simple HTML paragraph, auto-generate subject
        html_body = f"<p>{message_text}</p>"
        tool_params = {
            "to_address": to_address,
            "subject": "Re: Follow-up",
            "mobile_first_html": html_body,
        }
        if will_continue_work:
            tool_params["will_continue_work"] = True
        return (
            {
                "id": "implied_send",
                "function": {"name": "send_email", "arguments": json.dumps(tool_params)},
            },
            None,
        )

    elif channel == "peer_dm":
        peer_agent_id = ctx.get("peer_agent_id")
        if not peer_agent_id:
            return None, "Implied send failed: peer agent ID not available."
        tool_params = {"peer_agent_id": peer_agent_id, "message": message_text}
        if will_continue_work:
            tool_params["will_continue_work"] = True
        return (
            {
                "id": "implied_send",
                "function": {"name": "send_agent_message", "arguments": json.dumps(tool_params)},
            },
            None,
        )

    return None, f"Implied send failed: unsupported channel '{channel}'."

def _attempt_cycle_close_for_sleep(agent: PersistentAgent, budget_ctx: Optional[BudgetContext]) -> None:
    """Best-effort attempt to close the budget cycle when the agent goes idle."""

    if budget_ctx is None:
        return

    # If pending follow-ups are queued, keep the cycle open so they can run.
    try:
        redis_client = get_redis_client()
        if is_agent_pending(agent.id, client=redis_client):
            logger.info(
                "Agent %s sleeping with pending work queued; keeping cycle active.",
                agent.id,
            )
            return
    except Exception:
        logger.debug("Pending-flag check failed; proceeding to default close logic", exc_info=True)

    try:
        current_depth = (
            AgentBudgetManager.get_branch_depth(
                agent_id=budget_ctx.agent_id,
                branch_id=budget_ctx.branch_id,
            )
            or 0
        )
    except Exception:
        current_depth = getattr(budget_ctx, "depth", 0) or 0

    if current_depth > 0:
        logger.info(
            "Agent %s sleeping with %s outstanding child tasks; leaving cycle active.",
            agent.id,
            current_depth,
        )
        return

    try:
        AgentBudgetManager.close_cycle(
            agent_id=budget_ctx.agent_id, budget_id=budget_ctx.budget_id
        )
    except Exception:
        logger.debug("Failed to close budget cycle on sleep", exc_info=True)


def _runtime_exceeded(started_at: float, max_runtime_seconds: int) -> bool:
    if max_runtime_seconds <= 0:
        return False
    return (time.monotonic() - started_at) >= max_runtime_seconds


def _estimate_message_tokens(messages: List[dict]) -> int:
    """Estimate token count for a list of messages using simple heuristics."""
    total_text = ""
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total_text += content + " "

    # Rough estimation: ~4 characters per token (conservative estimate)
    estimated_tokens = len(total_text) // 4
    return max(estimated_tokens, 100)  # Minimum of 100 tokens


def _estimate_agent_context_tokens(agent: PersistentAgent) -> int:
    """Estimate token count for agent context using simple heuristics."""
    total_length = 0
    tool_result_overhead = 240

    # Charter length
    if agent.charter:
        total_length += len(agent.charter)

    # Rough estimates for other content
    # History: estimate based on recent steps and comms
    recent_steps = (
        PersistentAgentStep.objects.filter(agent=agent)
        .select_related("tool_call")
        .only("description", "tool_call__tool_name")
        .order_by('-created_at')[:10]
    )
    for step in recent_steps:
        # Add description length
        if step.description:
            total_length += len(step.description)

        # Account for tool result metadata (prompt stores metadata + small previews)
        try:
            if step.tool_call:
                total_length += tool_result_overhead
        except PersistentAgentToolCall.DoesNotExist:
            pass

    recent_comms = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .only("body")
        .order_by('-timestamp')[:5]
    )
    for comm in recent_comms:
        if comm.body:
            total_length += len(comm.body)

    # Add base overhead for system prompt and structure
    total_length += 2000  # Base system prompt overhead

    # Rough estimation: ~4 characters per token 
    estimated_tokens = total_length // 4

    # Apply reasonable bounds
    return max(min(estimated_tokens, 50000), 1000)  # Between 1k and 50k tokens


def _stream_completion_with_broadcast(
    *,
    model: str,
    messages: List[dict],
    params: dict,
    tools: Optional[List[dict]],
    provider: Optional[str],
    stream_broadcaster: Optional[WebStreamBroadcaster],
) -> Any:
    if stream_broadcaster:
        stream_broadcaster.start()

    content_filter = _CanonicalContinuationStreamFilter() if stream_broadcaster else None
    accumulator = StreamAccumulator()
    start_time = time.monotonic()
    try:
        stream = run_completion(
            model=model,
            messages=messages,
            params=params,
            tools=tools,
            drop_params=True,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            reasoning_delta, content_delta = accumulator.ingest_chunk(chunk)
            if stream_broadcaster:
                filtered_delta = content_filter.ingest(content_delta) if content_filter else content_delta
                stream_broadcaster.push_delta(reasoning_delta, filtered_delta)
    finally:
        if stream_broadcaster:
            trailing = content_filter.flush() if content_filter else None
            if trailing:
                stream_broadcaster.push_delta(None, trailing)
            stream_broadcaster.finish()

    response = accumulator.build_response(model=model, provider=provider)
    response.request_duration_ms = int(round((time.monotonic() - start_time) * 1000))
    raise_if_empty_litellm_response(response, model=model, provider=provider)
    raise_if_invalid_litellm_response(response, model=model, provider=provider)
    return response


_GEMINI_CACHE_MANAGER = GeminiCachedContentManager()
_GEMINI_CACHE_BLOCKLIST = GEMINI_CACHE_BLOCKLIST


def _completion_with_failover(
    messages: List[dict],
    tools: List[dict],
    failover_configs: List[Tuple[str, str, dict]],
    agent_id: str = None,
    safety_identifier: str = None,
    preferred_config: Optional[Tuple[str, str]] = None,
    stream_broadcaster: Optional[WebStreamBroadcaster] = None,
) -> Tuple[dict, Optional[dict]]:
    """
    Execute LLM completion with a pre-determined, tiered failover configuration.
    
    Args:
        messages: Chat messages for the LLM
        tools: Available tools for the LLM
        failover_configs: Pre-selected list of provider configurations
        agent_id: Optional agent ID for logging
        safety_identifier: Optional user ID for safety filtering
        preferred_config: Optional tuple of (provider, model) to try first
        stream_broadcaster: Optional broadcaster for streaming deltas to web UI
        
    Returns:
        Tuple of (LiteLLM completion response or streaming aggregate, token usage dict)
        Token usage dict contains: prompt_tokens, completion_tokens, total_tokens, 
        cached_tokens (optional), model, provider
        
    Raises:
        Exception: If all providers in all tiers fail
    """
    last_exc: Exception | None = None
    base_messages: List[dict] = list(messages or [])
    base_tools: List[dict] = list(tools or [])
    active_stream_broadcaster = stream_broadcaster

    ordered_configs: List[Tuple[str, str, dict]] = list(failover_configs)
    if preferred_config:
        pref_provider, pref_model = preferred_config
        full_match: List[Tuple[str, str, dict]] = []
        fallback: List[Tuple[str, str, dict]] = []
        for cfg in ordered_configs:
            cfg_provider, cfg_model, _ = cfg
            match_provider = cfg_provider == pref_provider
            match_model = cfg_model == pref_model
            if match_provider and match_model:
                full_match.append(cfg)
            else:
                fallback.append(cfg)
        if full_match:
            ordered_configs = full_match + fallback
            logger.info(
                "Applying preferred provider/model %s/%s for agent %s",
                pref_provider,
                pref_model,
                agent_id or "unknown",
            )
        else:
            logger.debug(
                "Preferred provider/model %s/%s not present for agent %s",
                pref_provider,
                pref_model,
                agent_id or "unknown",
            )

    for provider, model, params_with_hints in ordered_configs:
        logger.info(
            "Attempting provider %s for agent %s",
            provider,
            agent_id or "unknown",
        )

        try:
            with tracer.start_as_current_span("LLM Completion") as llm_span:
                if agent_id:
                    llm_span.set_attribute("persistent_agent.id", str(agent_id))
                llm_span.set_attribute("llm.model", model)
                llm_span.set_attribute("llm.provider", provider)
                params_base = dict(params_with_hints or {})
                params = dict(params_base)

                # Extra diagnostics for OpenAI-compatible / custom bases
                api_base = getattr(params, 'get', lambda *_: None)("api_base") if isinstance(params, dict) else None
                api_key_present = isinstance(params, dict) and bool(params.get("api_key"))
                if api_base:
                    llm_span.set_attribute("llm.api_base", api_base)
                llm_span.set_attribute("llm.api_key_present", bool(api_key_present))
                try:
                    masked = None
                    if api_key_present:
                        k = params.get("api_key")
                        masked = (str(k)[:6] + "…") if k else None
                    logger.info(
                        "LLM call: provider=%s model=%s api_base=%s api_key=%s",
                        provider,
                        model,
                        api_base or "",
                        masked or "<none>",
                    )
                except Exception:
                    pass

                # If OpenAI family, add safety_identifier hint when available
                request_messages = base_messages
                request_tools_payload: Optional[List[dict]] = list(base_tools) if base_tools else None
                use_gemini_cache = False

                if (provider.startswith("openai") or provider == "openai") and safety_identifier:
                    params["safety_identifier"] = str(safety_identifier)

                if active_stream_broadcaster:
                    try:
                        response = _stream_completion_with_broadcast(
                            model=model,
                            messages=request_messages,
                            params=params,
                            tools=request_tools_payload,
                            provider=provider,
                            stream_broadcaster=active_stream_broadcaster,
                        )
                    except Exception:
                        logger.warning(
                            "Streaming completion failed for provider=%s model=%s; retrying without streaming",
                            provider,
                            model,
                            exc_info=True,
                        )
                        active_stream_broadcaster.finish()
                        active_stream_broadcaster = None
                        response = run_completion(
                            model=model,
                            messages=request_messages,
                            params=params,
                            tools=request_tools_payload,
                            drop_params=True,
                        )
                else:
                    response = run_completion(
                        model=model,
                        messages=request_messages,
                        params=params,
                        tools=request_tools_payload,
                        drop_params=True,
                    )

                logger.info(
                    "Provider %s succeeded for agent %s",
                    provider,
                    agent_id or "unknown",
                )

                token_usage, usage = extract_token_usage(
                    response,
                    model=model,
                    provider=provider,
                )
                set_usage_span_attributes(llm_span, usage)

                return response, token_usage

        except Exception as exc:
            if use_gemini_cache and is_gemini_cache_conflict_error(exc):
                disable_gemini_cache_for(provider, model)
            last_exc = exc
            current_span = trace.get_current_span()
            mark_span_failed_with_exception(current_span, exc, f"LLM completion failed with {provider}")
            try:
                logger.exception(
                    "LLM call failed: provider=%s model=%s api_base=%s error=%s",
                    provider,
                    model,
                    api_base or (params.get('api_base') if isinstance(params, dict) else ''),
                    str(exc),
                )
            except Exception:
                pass
            logger.exception(
                "Provider %s failed for agent %s; trying next provider",
                provider,
                agent_id or "unknown",
            )

    # All providers failed
    if last_exc:
        raise last_exc
    raise RuntimeError("No LLM provider available")


def _get_completed_process_run_count(agent: Optional[PersistentAgent]) -> int:
    """Return how many PROCESS_EVENTS loops completed for the agent."""
    if agent is None:
        return 0

    return PersistentAgentSystemStep.objects.filter(
        step__agent=agent,
        code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        step__description="Process events",
    ).count()


def _get_recent_preferred_config(
    agent: PersistentAgent,
    run_sequence_number: int,
) -> Optional[Tuple[str, str]]:
    """
    Return the (provider, model) from the most recent completion if fresh enough.
    """
    if agent is None:
        return None

    if run_sequence_number == 2:
        # Skip preferred provider on second run to avoid immediate repetition
        return None

    max_streak_limit = getattr(settings, "MAX_PREFERRED_PROVIDER_STREAK", 20)

    streak_sample_size = max(1, max_streak_limit)

    try:
        recent_completions = list(
            PersistentAgentCompletion.objects.filter(
                agent=agent,
                completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
            )
            .only("created_at", "llm_model", "llm_provider")
            .order_by("-created_at")[:streak_sample_size]
        )
    except Exception:
        logger.debug(
            "Unable to determine last completion for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )
        return None

    if not recent_completions:
        return None

    window_start = dj_timezone.now() - PREFERRED_PROVIDER_MAX_AGE
    last_completion = recent_completions[0]
    last_model = getattr(last_completion, "llm_model", None)
    last_provider = getattr(last_completion, "llm_provider", None)
    agent_id = getattr(agent, "id", None)
    created_at = getattr(last_completion, "created_at", None)

    if not created_at or created_at < window_start:
        logger.info(
            "Agent %s preferred provider stale due to age (created_at=%s)",
            agent_id,
            created_at,
        )
        return None

    # Invalidate preferred provider if LLM config has changed since last completion
    try:
        LLMRoutingProfile = apps.get_model("api", "LLMRoutingProfile")
        active_profile = LLMRoutingProfile.objects.filter(is_active=True).only("updated_at").first()
        if active_profile and active_profile.updated_at and created_at < active_profile.updated_at:
            logger.info(
                "Agent %s preferred provider stale due to config change (completion=%s, config_updated=%s)",
                agent_id,
                created_at,
                active_profile.updated_at,
            )
            return None
    except Exception:
        logger.debug(
            "Unable to check LLM config staleness for agent %s",
            agent_id,
            exc_info=True,
        )

    if last_model and last_provider:
        streak = 0
        for completion in recent_completions:
            if (
                getattr(completion, "llm_model", None) == last_model
                and getattr(completion, "llm_provider", None) == last_provider
            ):
                streak += 1
            else:
                break
        if max_streak_limit is not None and streak >= max_streak_limit:
            logger.info(
                "Agent %s skipping preferred provider/model %s/%s due to streak=%d (limit=%d)",
                agent_id,
                last_provider,
                last_model,
                streak,
                max_streak_limit,
            )
            return None

        logger.info(
            "Agent %s reusing provider %s with model %s",
            agent_id,
            last_provider,
            last_model,
        )
        return last_provider, last_model

    logger.info(
        "Agent %s missing provider/model data for preferred config",
        agent_id,
    )
    return None


def _filter_preferred_config_for_low_latency(
    preferred_config: Optional[Tuple[str, str]],
    failover_configs: List[Tuple[str, str, dict]],
    *,
    agent_id: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    if not preferred_config:
        return None
    pref_provider, pref_model = preferred_config
    for provider, model, params in failover_configs:
        if provider == pref_provider and model == pref_model:
            if params.get("low_latency"):
                return preferred_config
            logger.info(
                "Agent %s skipping preferred provider/model %s/%s due to low-latency routing",
                agent_id or "unknown",
                pref_provider,
                pref_model,
            )
            return None
    return None


@retry(
    wait=wait_random_exponential(multiplier=1, max=60),
    stop=stop_after_attempt(3),  # Reduced retries since we have failover
    retry=retry_if_exception_type(
        (
            litellm.RateLimitError,
            litellm.ServiceUnavailableError,
            litellm.APIConnectionError,
            litellm.Timeout,
            # Note: Internal server errors and generic API errors are now handled by failover
        )
    ),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _completion_with_backoff(**kwargs):
    """
    Legacy wrapper around litellm.completion with exponential backoff.
    
    This is kept for backward compatibility, but _completion_with_failover
    is preferred for new code as it provides better fault tolerance.

    NOTE: As of 9/9/2025, this seems unused. If use is reinstated, ensure safety_identifier is an argument
    """
    return litellm.completion(**kwargs)


# --------------------------------------------------------------------------- #
#  Tool rate limit utilities
# --------------------------------------------------------------------------- #
def _resolve_tool_hourly_limit(agent: PersistentAgent, tool_name: str) -> Optional[int]:
    """Return the hourly limit for the tool based on the agent's plan."""
    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    if owner is None:
        return None

    try:
        settings = get_tool_settings_for_owner(owner)
        return settings.hourly_limit_for_tool(tool_name) if settings else None
    except DatabaseError:
        logger.error(
            "Failed to resolve tool rate limit for agent %s tool %s",
            getattr(agent, "id", None),
            tool_name,
            exc_info=True,
        )
        return None


def _enforce_tool_rate_limit(
    agent: PersistentAgent,
    tool_name: str,
    span=None,
    attach_completion=None,
    attach_prompt_archive=None,
) -> bool:
    """Enforce per-agent hourly rate limits; returns True if execution may proceed."""
    limit = _resolve_tool_hourly_limit(agent, tool_name)
    if limit is None:
        return True

    cutoff = dj_timezone.now() - timedelta(hours=1)
    try:
        recent_count = (
            PersistentAgentToolCall.objects.filter(
                step__agent=agent,
                tool_name=tool_name,
                step__created_at__gte=cutoff,
            ).count()
        )
    except DatabaseError:
        logger.error(
            "Failed to evaluate rate limit for agent %s tool %s",
            getattr(agent, "id", None),
            tool_name,
            exc_info=True,
        )
        return True

    if recent_count < limit:
        return True

    limit_display = limit
    msg_desc = (
        f"Skipped tool '{tool_name}' due to hourly limit. "
        f"{recent_count} of {limit_display} calls in the past hour."
    )
    step_kwargs = {
        "agent": agent,
        "description": msg_desc,
    }
    if attach_completion:
        try:
            attach_completion(step_kwargs)
        except Exception:
            logger.warning(
                "Failed to attach completion while recording tool rate limit for agent %s tool %s",
                getattr(agent, "id", None),
                tool_name,
                exc_info=True,
            )
    step = PersistentAgentStep.objects.create(**step_kwargs)
    if attach_prompt_archive:
        try:
            attach_prompt_archive(step)
        except Exception:
            logger.debug(
                "Failed to attach prompt archive for tool rate limit step %s",
                getattr(step, "id", None),
                exc_info=True,
            )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.RATE_LIMIT,
        notes="tool_hourly_rate_limit",
    )
    logger.warning(
        "Agent %s skipped tool %s due to hourly rate limit (recent=%s limit=%s)",
        agent.id,
        tool_name,
        recent_count,
        limit_display,
    )
    if span is not None:
        try:
            span.add_event("Tool skipped - hourly rate limit reached")
            span.set_attribute("tool_rate_limit.limit", int(limit_display))
            span.set_attribute("tool_rate_limit.recent_count", int(recent_count))
        except Exception:
            logger.debug("Failed to add attributes to span for tool rate limit", exc_info=True)
    return False


# --------------------------------------------------------------------------- #
#  Credit gating utilities
# --------------------------------------------------------------------------- #
def _has_sufficient_daily_credit(state: dict, cost: Decimal | None) -> bool:
    """Return True if the daily credit limit permits the additional cost."""

    if cost is None:
        return True

    hard_limit = state.get("hard_limit")
    if hard_limit is None:
        return True

    remaining = state.get("hard_limit_remaining")
    if remaining is None:
        try:
            used = state.get("used", Decimal("0"))
            if not isinstance(used, Decimal):
                used = Decimal(str(used))
            remaining = hard_limit - used
        except Exception as exc:
            logger.warning("Failed to derive hard limit remaining: %s", exc)
            remaining = Decimal("0")

    try:
        return remaining >= cost
    except TypeError as e:
        logger.warning("Type error during daily credit check: %s", e)
        return False


def _ensure_credit_for_tool(
    agent: PersistentAgent,
    tool_name: str,
    span=None,
    credit_snapshot: Optional[Dict[str, Any]] = None,
) -> dict[str, Any] | Literal[False]:
    """Ensure the agent's owner has a task credit and consume it just-in-time.

    Returns False if insufficient or consumption fails. On success, returns a dict
    containing the consumed cost and the TaskCredit (if any), so callers can attach
    them to persisted steps for accurate usage attribution.
    """
    if tool_name == "send_chat_message":
        return {"cost": None, "credit": None}

    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    owner_is_org = TaskCreditService._is_organization_owner(owner) if owner is not None else False
    owner_user = getattr(agent, "user", None)
    owner_label = (
        f"organization {getattr(owner, 'id', 'unknown')}"
        if owner_is_org
        else f"user {getattr(owner_user, 'id', 'unknown')}"
    )

    if not settings.GOBII_PROPRIETARY_MODE or owner is None:
        return {"cost": None, "credit": None}

    cost: Decimal | None = None
    consumed: dict | None = None
    consumed_credit = None

    # Determine tool cost up-front so we can gate on fractional balances
    try:
        cost = get_tool_credit_cost(tool_name)
    except Exception as e:
        logger.warning(
            "Failed to get credit cost for tool '%s', falling back to default. Error: %s",
            tool_name, e, exc_info=True
        )
        # Fallback to default single-task cost when lookup fails
        cost = get_default_task_credit_cost()

    if cost is not None:
        cost = apply_tier_credit_multiplier(agent, cost)

    if credit_snapshot is not None and "available" in credit_snapshot:
        available = credit_snapshot.get("available")
    else:
        try:
            available = TaskCreditService.calculate_available_tasks_for_owner(owner)
        except Exception as e:
            logger.error(
                "Credit availability check (in-loop) failed for agent %s (%s): %s",
                agent.id,
                owner_label,
                str(e),
            )
            available = None
        if credit_snapshot is not None:
            credit_snapshot["available"] = available

    daily_state = None
    if credit_snapshot is not None and isinstance(credit_snapshot.get("daily_state"), dict):
        daily_state = credit_snapshot["daily_state"]
    if daily_state is None:
        daily_state = get_agent_daily_credit_state(agent)
        if credit_snapshot is not None:
            credit_snapshot["daily_state"] = daily_state

    hard_limit = daily_state.get("hard_limit")
    hard_remaining = daily_state.get("hard_limit_remaining")
    soft_target = daily_state.get("soft_target")
    soft_target_remaining = daily_state.get("soft_target_remaining")
    soft_exceeded = daily_state.get("soft_target_exceeded")

    if soft_exceeded and not daily_state.get("soft_target_warning_logged"):
        daily_state["soft_target_warning_logged"] = True
        logger.info(
            "Agent %s exceeded daily soft target (used=%s target=%s)",
            agent.id,
            daily_state.get("used"),
            soft_target,
        )
        try:
            analytics_props: dict[str, Any] = {
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "tool_name": tool_name,
                "message_type": "task_credits_low",
                "medium": "backend",
            }
            if soft_target is not None:
                analytics_props["soft_target"] = str(soft_target)
            used_value = daily_state.get("used")
            if used_value is not None:
                analytics_props["credits_used_today"] = str(used_value)
            if soft_target_remaining is not None:
                analytics_props["soft_target_remaining"] = str(soft_target_remaining)
            props_with_org = Analytics.with_org_properties(
                analytics_props,
                organization=getattr(agent, "organization", None),
            )
            Analytics.track_event(
                user_id=owner_user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_SOFT_LIMIT_EXCEEDED,
                source=AnalyticsSource.AGENT,
                properties=props_with_org,
            )
        except Exception:
            logger.exception(
                "Failed to emit analytics for agent %s soft target exceedance",
                agent.id,
            )
        if span is not None:
            try:
                span.add_event("Soft target exceeded")
            except Exception:
                pass

    if span is not None:
        try:
            span.set_attribute(
                "credit_check.available_in_loop",
                int(available) if available is not None else -2,
            )
        except Exception as e:
            logger.debug("Failed to set soft target span attributes: %s", e)
        try:
            span.set_attribute(
                "credit_check.owner_type",
                "organization" if owner_is_org else "user",
            )
            if owner_is_org:
                span.set_attribute("credit_check.organization_id", str(getattr(owner, "id", None)))
            if owner_user is not None:
                span.set_attribute("credit_check.user_id", str(owner_user.id))
        except Exception as e:
            logger.debug("Failed to set owner span attributes: %s", e)
        try:
            span.set_attribute(
                "credit_check.tool_cost",
                float(cost) if cost is not None else float(get_default_task_credit_cost()),
            )
        except Exception as e:
            logger.debug("Failed to set span attribute 'credit_check.tool_cost': %s", e)
        try:
            span.set_attribute(
                "credit_check.daily_limit",
                float(hard_limit) if hard_limit is not None else -1.0,
            )
        except Exception as e:
            logger.debug("Failed to set span attribute 'credit_check.daily_limit': %s", e)
        try:
            span.set_attribute(
                "credit_check.daily_remaining_before",
                float(hard_remaining) if hard_remaining is not None else -1.0,
            )
        except Exception as e:
            logger.debug("Failed to set span attribute 'credit_check.daily_remaining_before': %s", e)
        try:
            span.set_attribute(
                "credit_check.daily_soft_target",
                float(soft_target) if soft_target is not None else -1.0,
            )
            span.set_attribute(
                "credit_check.daily_soft_remaining",
                float(soft_target_remaining) if soft_target_remaining is not None else -1.0,
            )
            span.set_attribute(
                "credit_check.daily_soft_exceeded",
                bool(soft_exceeded),
            )
        except Exception:
            pass

    if not _has_sufficient_daily_credit(daily_state, cost):
        if not daily_state.get("hard_limit_warning_logged"):
            daily_state["hard_limit_warning_logged"] = True
            try:
                analytics_props: dict[str, Any] = {
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "tool_name": tool_name,
                    "message_type": "daily_hard_limit",
                    "medium": "backend",
                }
                if hard_limit is not None:
                    analytics_props["hard_limit"] = str(hard_limit)
                used_value = daily_state.get("used")
                if used_value is not None:
                    analytics_props["credits_used_today"] = str(used_value)
                if hard_remaining is not None:
                    analytics_props["hard_limit_remaining"] = str(hard_remaining)
                props_with_org = Analytics.with_org_properties(
                    analytics_props,
                    organization=getattr(agent, "organization", None),
                )
                Analytics.track_event(
                    user_id=owner_user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_HARD_LIMIT_EXCEEDED,
                    source=AnalyticsSource.AGENT,
                    properties=props_with_org,
                )
            except Exception:
                logger.exception(
                    "Failed to emit analytics for agent %s hard limit exceedance",
                    agent.id,
                )
        limit_display = hard_limit
        used_display = daily_state.get("used")
        msg_desc = (
            f"Skipped tool '{tool_name}' because this agent reached its enforced daily credit limit for today."
        )
        if limit_display is not None:
            msg_desc += f" {used_display} of {limit_display} credits already used."

        step = PersistentAgentStep.objects.create(
            agent=agent,
            description=msg_desc,
        )
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="daily_credit_limit_mid_loop",
        )
        send_owner_daily_credit_hard_limit_notice(agent)
        if span is not None:
            try:
                span.add_event("Tool skipped - daily credit limit reached")
                span.set_attribute("credit_check.daily_limit_block", True)
            except Exception:
                pass
        logger.warning(
            "Agent %s skipped tool %s due to daily credit limit (used=%s limit=%s)",
            agent.id,
            tool_name,
            used_display,
            limit_display,
        )
        return False

    if (
        available is not None
        and available != TASKS_UNLIMITED
        and cost is not None
        and Decimal(available) < cost
    ):
        msg_desc = (
            f"Skipped tool '{tool_name}' due to insufficient credits mid-loop."
        )
        step = PersistentAgentStep.objects.create(
            agent=agent,
            description=msg_desc,
        )
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="credit_insufficient_mid_loop",
        )
        if span is not None:
            try:
                span.add_event("Tool skipped - insufficient credits mid-loop")
            except Exception:
                pass
        logger.warning(
            "Agent %s insufficient credits mid-loop; halting further processing.",
            agent.id,
        )
        return False

    try:
        with transaction.atomic():
            consumed = TaskCreditService.check_and_consume_credit_for_owner(owner, amount=cost)
            consumed_credit = consumed.get("credit") if consumed else None
    except Exception as e:
        logger.error(
            "Credit consumption (in-loop) failed for agent %s (%s): %s",
            agent.id,
            owner_label,
            str(e),
        )
        if span is not None:
            try:
                span.add_event("Credit consumption raised exception", {"error": str(e)})
                span.set_attribute("credit_check.error", str(e))
            except Exception:
                pass

    if span is not None:
        try:
            span.set_attribute("credit_check.consumed_in_loop", bool(consumed and consumed.get('success')))
        except Exception:
            pass
    if not consumed or not consumed.get('success'):
        msg_desc = (
            f"Skipped tool '{tool_name}' due to insufficient credits during processing."
        )
        step = PersistentAgentStep.objects.create(
            agent=agent,
            description=msg_desc,
        )
        PersistentAgentSystemStep.objects.create(
            step=step,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes="credit_consumption_failure_mid_loop",
        )
        if span is not None:
            try:
                span.add_event("Tool skipped - insufficient credits during processing")
            except Exception:
                pass
        logger.warning(
            "Agent %s encountered insufficient credits during processing; halting further processing.",
            agent.id,
        )
        return False

    # Update the cached daily state immediately so subsequent tool calls in the same batch
    # see the cost impact (DB-backed aggregation lags until the step is persisted).
    if cost is not None and isinstance(daily_state, dict):
        try:
            used_value = daily_state.get("used", Decimal("0"))
            if not isinstance(used_value, Decimal):
                used_value = Decimal(str(used_value))
            new_used = used_value + cost
            daily_state["used"] = new_used

            # Recompute remaining fields (best-effort; do not fail tool execution).
            hard_limit_value = daily_state.get("hard_limit")
            if hard_limit_value is not None:
                hard_remaining_after = hard_limit_value - new_used
                daily_state["hard_limit_remaining"] = (
                    hard_remaining_after if hard_remaining_after > Decimal("0") else Decimal("0")
                )
            soft_target_value = daily_state.get("soft_target")
            if soft_target_value is not None:
                soft_remaining_after = soft_target_value - new_used
                soft_remaining_after = (
                    soft_remaining_after if soft_remaining_after > Decimal("0") else Decimal("0")
                )
                daily_state["soft_target_remaining"] = soft_remaining_after
                daily_state["remaining"] = soft_remaining_after
                daily_state["soft_target_exceeded"] = soft_remaining_after <= Decimal("0")
        except Exception:
            logger.debug(
                "Failed to update cached daily_state after consuming credit for agent %s",
                agent.id,
                exc_info=True,
            )

    if credit_snapshot is not None:
        credit_snapshot["daily_state"] = daily_state
        # Force a fresh account-wide balance lookup next time.
        credit_snapshot.pop("available", None)

    if span is not None:
        try:
            remaining_after = (
                daily_state.get("hard_limit_remaining") if isinstance(daily_state, dict) else None
            )
            span.set_attribute(
                "credit_check.daily_remaining_after",
                float(remaining_after) if remaining_after is not None else -1.0,
            )
        except Exception:
            pass

    return {
        "cost": cost,
        "credit": consumed_credit,
    }


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def process_agent_events(
    persistent_agent_id: Union[str, UUID],
    budget_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    depth: Optional[int] = None,
    eval_run_id: Optional[str] = None,
    mock_config: Optional[Dict[str, Any]] = None,
    burn_follow_up_token: Optional[str] = None,
    worker_pid: Optional[int] = None,
) -> None:
    """Process all outstanding events for a persistent agent."""
    normalized_agent_id = _normalize_persistent_agent_id(persistent_agent_id)
    if not normalized_agent_id:
        logger.warning(
            "process_agent_events called with invalid agent id: %s",
            persistent_agent_id,
        )
        return
    persistent_agent_id = normalized_agent_id

    span = trace.get_current_span()
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))

    logger.info("process_agent_events(%s) called", persistent_agent_id)

    redis_client = get_redis_client()
    follow_up_key = burn_follow_up_key(persistent_agent_id)
    cooldown_key = burn_cooldown_key(persistent_agent_id)

    # If this invocation is a scheduled burn-rate follow-up, ensure the token matches.
    if burn_follow_up_token:
        stored_token = redis_client.get(follow_up_key)
        stored_token_value = (
            stored_token.decode() if isinstance(stored_token, (bytes, bytearray)) else stored_token
        )
        if not stored_token_value or stored_token_value != burn_follow_up_token:
            logger.info(
                "Skipping burn-rate follow-up for agent %s (token missing or mismatched).",
                persistent_agent_id,
            )
            span.add_event("Burn-rate follow-up skipped - token mismatch")
            return
        try:
            redis_client.delete(follow_up_key)
        except Exception:
            logger.debug(
                "Failed to clear burn follow-up token for agent %s", persistent_agent_id, exc_info=True
            )
    else:
        # Respect active burn-rate cooldown unless a recent user message arrived.
        try:
            cooldown_value = redis_client.get(cooldown_key)
            cooldown_active = bool(cooldown_value)
        except Exception:
            logger.warning(
                "Failed to read burn-rate cooldown for agent %s; proceeding as if inactive.",
                persistent_agent_id,
                exc_info=True,
            )
            cooldown_active = False
        if cooldown_active:
            if has_recent_user_message(
                persistent_agent_id,
                window_minutes=BURN_RATE_USER_INACTIVITY_MINUTES,
            ):
                try:
                    redis_client.delete(cooldown_key)
                except Exception:
                    logger.debug(
                        "Failed to clear burn cooldown for agent %s after user interaction",
                        persistent_agent_id,
                        exc_info=True,
                    )
            else:
                logger.info(
                    "Skipping event processing for agent %s – burn-rate cooldown active.",
                    persistent_agent_id,
                )
                span.add_event("Processing skipped - burn cooldown active")
                clear_processing_queued_flag(persistent_agent_id)
                return

        # A normal run cancels any pending burn follow-up so we don't double-run.
        try:
            deleted = redis_client.delete(follow_up_key)
            if isinstance(deleted, int) and deleted > 0:
                logger.info(
                    "Cleared pending burn-rate follow-up token for agent %s due to new processing run.",
                    persistent_agent_id,
                )
        except redis.exceptions.RedisError as e:
            logger.warning(
                "Failed to clear burn-rate follow-up token for agent %s: %s", persistent_agent_id, e, exc_info=True
            )

    # Guard against reviving expired/closed cycles when a follow‑up arrives after TTL expiry
    if budget_id is not None:
        status = AgentBudgetManager.get_cycle_status(agent_id=str(persistent_agent_id))
        active_id = AgentBudgetManager.get_active_budget_id(agent_id=str(persistent_agent_id))
        if status != "active" or active_id != budget_id:
            logger.info(
                "Ignoring follow-up for agent %s: cycle %s is %s (active=%s)",
                persistent_agent_id,
                budget_id,
                status or "expired",
                active_id or "none",
            )
            return

    # ---------------- Budget context bootstrap ---------------- #
    # If this is a top-level trigger (no budget provided), start/reuse a cycle.
    if budget_id is None:
        budget_id, max_steps, max_depth = AgentBudgetManager.find_or_start_cycle(
            agent_id=str(persistent_agent_id)
        )
        # Top-level depth defaults to 0 and gets its own branch
        depth = 0 if depth is None else depth
        branch_id = AgentBudgetManager.create_branch(
            agent_id=str(persistent_agent_id), budget_id=budget_id, depth=depth
        )
    else:
        # Budget already exists – read limits for context
        max_steps, max_depth = AgentBudgetManager.get_limits(agent_id=str(persistent_agent_id))
        if depth is None:
            depth = 0
        # If branch is missing (shouldn't be typical), create one at provided depth
        if branch_id is None:
            branch_id = AgentBudgetManager.create_branch(
                agent_id=str(persistent_agent_id), budget_id=budget_id, depth=depth
            )

    # Phase 1 (soft): validate branch existence and decouple counters
    # We treat the stored branch "depth" as an outstanding-children counter.
    # Do NOT overwrite recursion depth (ctx.depth) with the stored counter.
    try:
        stored_depth = AgentBudgetManager.get_branch_depth(
            agent_id=str(persistent_agent_id), branch_id=str(branch_id)
        )
        if stored_depth is None:
            # Initialize counter to 0 when missing; leave recursion depth unchanged
            AgentBudgetManager.set_branch_depth(
                agent_id=str(persistent_agent_id), branch_id=str(branch_id), depth=0
            )
            logger.warning(
                "Initialized missing branch counter for agent %s (branch_id=%s) to 0",
                persistent_agent_id,
                branch_id,
            )
        else:
            # Keep for diagnostics only
            logger.debug(
                "Branch counter present for agent %s (branch_id=%s): %s",
                persistent_agent_id,
                branch_id,
                stored_depth,
            )
    except Exception:
        logger.debug("Branch validation failed; proceeding softly", exc_info=True)

    ctx = BudgetContext(
        agent_id=str(persistent_agent_id),
        budget_id=str(budget_id),
        branch_id=str(branch_id),
        depth=int(depth),
        max_steps=int(max_steps),
        max_depth=int(max_depth),
        eval_run_id=eval_run_id,
        mock_config=mock_config,
    )
    set_budget_context(ctx)

    # Use distributed lock to ensure only one event processing call per agent
    lock_key = f"agent-event-processing:{persistent_agent_id}"
    lock_settings = _get_event_processing_lock_settings()

    lock = Redlock(
        key=lock_key,
        masters={redis_client},
        auto_release_time=lock_settings.lock_timeout_seconds,
        num_extensions=lock_settings.lock_max_extensions,
    )
    lock_extender = _LockExtender(
        lock,
        interval_seconds=lock_settings.lock_extend_interval_seconds,
        span=span,
    )

    lock_acquired = False
    processed_agent: Optional[PersistentAgent] = None
    heartbeat: Optional[_ProcessingHeartbeat] = None

    try:
        # Try to acquire the lock with a small timeout. If this instance cannot get the lock,
        # enqueue the agent ID for a debounced drain task to retry once the lock clears.
        if not lock.acquire(blocking=True, timeout=lock_settings.lock_acquire_timeout_seconds):
            if _maybe_clear_stale_lock(
                lock_key=lock_key,
                lock_timeout_seconds=lock_settings.lock_timeout_seconds,
                pending_set_ttl_seconds=lock_settings.pending_set_ttl_seconds,
                redis_client=redis_client,
                span=span,
            ):
                if lock.acquire(blocking=False):
                    lock_acquired = True
                else:
                    span.add_event("Stale lock cleared but reacquire failed")
            if not lock_acquired:
                enqueue_pending_agent(
                    persistent_agent_id,
                    ttl=lock_settings.pending_set_ttl_seconds,
                )

                logger.info(
                    "Skipping event processing for agent %s – another process is already handling events (queued pending)",
                    persistent_agent_id,
                )
                span.add_event("Event processing skipped – lock acquisition failed (pending queued)")
                span.set_attribute("lock.acquired", False)
                _schedule_pending_drain(
                    delay_seconds=lock_settings.pending_drain_delay_seconds,
                    schedule_ttl_seconds=lock_settings.pending_drain_schedule_ttl_seconds,
                    span=span,
                )
                return

        lock_acquired = True
        clear_processing_queued_flag(persistent_agent_id)
        if lock_settings.heartbeat_ttl_seconds > 0:
            if worker_pid is None:
                worker_pid = os.getpid()
            heartbeat = _ProcessingHeartbeat(
                agent_id=str(persistent_agent_id),
                ttl_seconds=lock_settings.heartbeat_ttl_seconds,
                started_at=time.time(),
                redis_client=redis_client,
                worker_pid=worker_pid,
            )
            heartbeat.touch("lock_acquired")

        logger.info("Acquired distributed lock for agent %s", persistent_agent_id)
        span.add_event("Distributed lock acquired")
        span.set_attribute("lock.acquired", True)

        # ---------------- SQLite state context ---------------- #
        with agent_sqlite_db(str(persistent_agent_id)) as _sqlite_db_path:
            # Optional: record path for debugging (will be in temp dir)
            span.set_attribute("sqlite_db.temp_path", _sqlite_db_path)

            # Actual event processing logic (protected by the lock)
            processed_agent = _process_agent_events_locked(
                persistent_agent_id,
                span,
                lock_extender=lock_extender,
                heartbeat=heartbeat,
            )

    except Exception as e:
        logger.error("Error during event processing for agent %s: %s", persistent_agent_id, str(e))
        span.add_event("Event processing error")
        span.set_attribute("processing.error", str(e))

        # Clean up budget on exceptions to prevent leaks
        if ctx and lock_acquired:
            try:
                AgentBudgetManager.close_cycle(
                    agent_id=ctx.agent_id,
                    budget_id=ctx.budget_id
                )
                logger.info("Closed budget cycle for agent %s due to exception", persistent_agent_id)
            except Exception as cleanup_error:
                logger.warning("Failed to close budget cycle on exception: %s", cleanup_error)

        raise
    finally:
        # Release the lock
        if lock_acquired:
            try:
                lock.release()
                logger.info("Released distributed lock for agent %s", persistent_agent_id)
                span.add_event("Distributed lock released")
            except Exception as e:
                logger.warning("Failed to release lock for agent %s: %s", persistent_agent_id, str(e))
                span.add_event("Lock release warning")
        if heartbeat:
            heartbeat.clear()

        # Clear local budget context
        set_budget_context(None)

        # Broadcast final processing state to websocket clients after all processing is complete
        try:
            from console.agent_chat.signals import _broadcast_processing

            agent_obj = processed_agent
            if agent_obj is None:
                agent_obj = PersistentAgent.objects.filter(id=persistent_agent_id).first()
            if agent_obj is not None:
                _broadcast_processing(agent_obj)
        except Exception as e:
            logger.debug("Failed to broadcast processing state for agent %s: %s", persistent_agent_id, e)


def _process_agent_events_locked(
    persistent_agent_id: Union[str, UUID],
    span,
    *,
    lock_extender: Optional[_LockExtender] = None,
    heartbeat: Optional[_ProcessingHeartbeat] = None,
) -> Optional[PersistentAgent]:
    """Core event processing logic, called while holding the distributed lock."""
    try:
        agent = (
            PersistentAgent.objects.select_related(
                "user",
                "preferred_contact_endpoint",
                "browser_use_agent",
            )
            .prefetch_related("webhooks")
            .get(id=persistent_agent_id)
        )
    except PersistentAgent.DoesNotExist:
        logger.warning("Persistent agent %s not found; skipping processing.", persistent_agent_id)
        return None

    # Broadcast processing state at start of processing (when lock is acquired)
    try:
        from console.agent_chat.signals import _broadcast_processing

        _broadcast_processing(agent)
    except Exception as e:
        logger.debug("Failed to broadcast processing state at start for agent %s: %s", persistent_agent_id, e)

    # Exit early in proprietary mode if the agent's owner has no credits
    credit_snapshot: Optional[Dict[str, Any]] = None
    try:

        if is_llm_bootstrap_required():
            msg = "Agent execution paused: LLM configuration required."
            logger.warning(
                "Persistent agent %s skipped – platform setup requires LLM credentials.",
                persistent_agent_id,
            )
            span.add_event("Agent processing skipped - llm bootstrap pending")
            span.set_attribute("llm.bootstrap_required", True)

            if not PersistentAgentSystemStep.objects.filter(
                step__agent=agent,
                code=PersistentAgentSystemStep.Code.LLM_CONFIGURATION_REQUIRED,
            ).exists():
                step = PersistentAgentStep.objects.create(
                    agent=agent,
                    description=msg,
                )
                PersistentAgentSystemStep.objects.create(
                    step=step,
                    code=PersistentAgentSystemStep.Code.LLM_CONFIGURATION_REQUIRED,
                    notes="llm_configuration_missing",
                )

            return agent

        # Extract routing profile ID for metadata tasks
        routing_profile = get_current_eval_routing_profile()
        routing_profile_id = str(routing_profile.id) if routing_profile else None

        try:
            maybe_schedule_short_description(agent, routing_profile_id=routing_profile_id)
        except Exception:
            logger.exception(
                "Failed to evaluate short description scheduling for agent %s",
                persistent_agent_id,
            )

        try:
            maybe_schedule_mini_description(agent, routing_profile_id=routing_profile_id)
        except Exception:
            logger.exception(
                "Failed to evaluate mini description scheduling for agent %s",
                persistent_agent_id,
            )
        try:
            maybe_schedule_agent_tags(agent, routing_profile_id=routing_profile_id)
        except Exception:
            logger.exception(
                "Failed to evaluate tag scheduling for agent %s",
                persistent_agent_id,
            )
        try:
            maybe_schedule_agent_avatar(agent, routing_profile_id=routing_profile_id)
        except Exception:
            logger.exception(
                "Failed to evaluate avatar scheduling for agent %s",
                persistent_agent_id,
            )

        if settings.GOBII_PROPRIETARY_MODE:
            owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
            owner_user = getattr(agent, "user", None)
            owner_is_org = TaskCreditService._is_organization_owner(owner) if owner is not None else False
            if owner is not None:
                owner_label = (
                    f"organization {getattr(owner, 'id', 'unknown')}"
                    if owner_is_org
                    else f"user {getattr(owner_user, 'id', 'unknown')}"
                )
                try:
                    available = TaskCreditService.calculate_available_tasks_for_owner(owner)
                except Exception as e:
                    # Defensive: if availability calc fails, log and proceed (do not block agent)
                    logger.error(
                        "Credit availability check failed for agent %s (%s): %s",
                        persistent_agent_id,
                        owner_label,
                        str(e),
                    )
                    available = None

                span.set_attribute("credit_check.available", int(available) if available is not None else 0)
                span.set_attribute("credit_check.proprietary_mode", True)
                span.set_attribute("credit_check.owner_type", "organization" if owner_is_org else "user")
                if owner_is_org:
                    span.set_attribute("credit_check.organization_id", str(getattr(owner, "id", None)))
                if owner_user is not None:
                    span.set_attribute("credit_check.user_id", owner_user.id)

                if (
                    available is not None
                    and available != TASKS_UNLIMITED
                    and Decimal(available) <= Decimal("0")
                ):
                    msg = f"Skipped processing due to insufficient credits (proprietary mode)."
                    logger.warning(
                        "Persistent agent %s not processed – %s has no remaining task credits.",
                        persistent_agent_id,
                        owner_label,
                    )

                    step = PersistentAgentStep.objects.create(
                        agent=agent,
                        description=msg,
                    )
                    PersistentAgentSystemStep.objects.create(
                        step=step,
                        code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                        notes="credit_insufficient",
                    )

                    span.add_event("Agent processing skipped - insufficient credits")
                    span.set_attribute("credit_check.sufficient", False)
                    return agent
                daily_state = get_agent_daily_credit_state(agent)
                daily_limit = daily_state.get("hard_limit")
                daily_remaining = daily_state.get("hard_limit_remaining")
                credit_snapshot = {
                    "available": available,
                    "daily_state": daily_state,
                }
                try:
                    span.set_attribute(
                        "credit_check.daily_limit",
                        float(daily_limit) if daily_limit is not None else -1.0,
                    )
                    span.set_attribute(
                        "credit_check.daily_remaining_before_loop",
                        float(daily_remaining) if daily_remaining is not None else -1.0,
                    )
                except Exception:
                    pass

                if daily_limit is not None and (daily_remaining is None or daily_remaining <= Decimal("0")):
                    msg = (
                        "Skipped processing because this agent has reached its enforced daily task credit limit."
                    )
                    logger.warning(
                        "Persistent agent %s not processed – hard daily limit reached (used=%s limit=%s).",
                        persistent_agent_id,
                        daily_state.get("used"),
                        daily_limit,
                    )

                    step = PersistentAgentStep.objects.create(
                        agent=agent,
                        description=msg,
                    )
                    PersistentAgentSystemStep.objects.create(
                        step=step,
                        code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                        notes="daily_credit_limit_exhausted",
                    )

                    send_owner_daily_credit_hard_limit_notice(agent)
                    span.add_event("Agent processing skipped - daily credit limit reached")
                    span.set_attribute("credit_check.daily_limit_block", True)
                    return agent
            else:
                # Agents without a linked user (system/automation) are not gated
                span.add_event("Agent has no owner; skipping credit gate")
        else:
            # Non-proprietary mode: do not gate on credits
            span.add_event("Proprietary mode disabled; skipping credit gate")

    except Exception as e:
        logger.error(f"Error during credit gate for agent {persistent_agent_id}: {str(e)}")
        span.add_event('Credit gate error')
        span.set_attribute('credit_check.error', str(e))
        return agent

    prior_run_count = _get_completed_process_run_count(agent)

    # Determine whether this is the first processing run before recording the system step
    is_first_run = prior_run_count == 0
    run_sequence_number = prior_run_count + 1

    try:
        publish_agent_event(str(agent.id), AgentEventType.PROCESSING_STARTED)

        with transaction.atomic():
            processing_step = PersistentAgentStep.objects.create(
                agent=agent,
                description="Process events",
            )
            sys_step = PersistentAgentSystemStep.objects.create(
                step=processing_step,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            )
        if heartbeat:
            heartbeat.update_run_id(str(processing_step.id))

        logger.info(
            "Processing agent %s (is_first_run=%s, run_sequence_number=%s)",
            agent.id,
            is_first_run,
            run_sequence_number,
        )
        span.set_attribute('processing_step.id', str(processing_step.id))
        span.set_attribute('processing.is_first_run', is_first_run)
        span.set_attribute('processing.run_sequence_number', run_sequence_number)

        cumulative_token_usage = _run_agent_loop(
            agent,
            is_first_run=is_first_run,
            credit_snapshot=credit_snapshot,
            run_sequence_number=run_sequence_number,
            lock_extender=lock_extender,
            heartbeat=heartbeat,
        )

        sys_step.notes = "simplified"
        try:
            sys_step.save(update_fields=["notes"])
        except OperationalError:
            close_old_connections()
            sys_step.save(update_fields=["notes"])
    finally:
        try:
            outstanding = AgentBudgetManager.get_total_outstanding_work(agent_id=str(agent.id))
            publish_agent_event(
                str(agent.id),
                AgentEventType.PROCESSING_COMPLETE,
                {"outstanding_tasks": outstanding}
            )
        except Exception:
            logger.exception("Failed to publish completion event for agent %s", agent.id)

    return agent

@tracer.start_as_current_span("Agent Loop")
def _run_agent_loop(
    agent: PersistentAgent,
    *,
    is_first_run: bool,
    credit_snapshot: Optional[Dict[str, Any]] = None,
    run_sequence_number: Optional[int] = None,
    lock_extender: Optional[_LockExtender] = None,
    heartbeat: Optional[_ProcessingHeartbeat] = None,
) -> dict:
    """The core tool‑calling loop for a persistent agent.
    
    Args:
        agent: Agent being processed.
        is_first_run: Whether this is the first ever processing run.
        credit_snapshot: Cached credit info for prompt context.
        run_sequence_number: 1-based count of PROCESS_EVENTS runs for the agent.
    
    Returns:
        dict: Cumulative token usage across all iterations
    """
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))
    logger.info("Starting agent loop for agent %s", agent.id)
    # Clear agent variables from any previous processing cycle
    clear_variables()
    span.set_attribute("burn.cooldown_seconds", BURN_RATE_COOLDOWN_SECONDS)
    max_runtime_seconds = int(getattr(settings, "AGENT_EVENT_PROCESSING_MAX_RUNTIME_SECONDS", 0))
    run_started_at = time.monotonic()
    if heartbeat:
        heartbeat.touch("loop_start")
    try:
        redis_client = get_redis_client()
    except Exception:
        logger.warning(
            "Failed to acquire Redis client for agent %s; burn controls may be impaired.",
            agent.id,
            exc_info=True,
        )
        redis_client = None
    burn_follow_up_task = globals().get("process_agent_events_task")
    span.set_attribute("burn.follow_up_task_present", bool(burn_follow_up_task))

    # Heuristic auto-enable: scan recent inbound messages for site keywords
    # and pre-enable relevant tools if there's capacity (no eviction)
    try:
        recent_messages = PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=False,
        ).order_by("-timestamp")[:3]
        combined_text = " ".join(msg.body for msg in recent_messages if msg.body)
        if combined_text:
            auto_enabled = auto_enable_heuristic_tools(agent, combined_text)
            if auto_enabled:
                span.set_attribute("autotool.enabled_count", len(auto_enabled))
                span.set_attribute("autotool.enabled_tools", ",".join(auto_enabled))
    except Exception:
        logger.debug("Autotool heuristic check failed", exc_info=True)

    tools = get_agent_tools(agent)

    # Track cumulative token usage across all iterations
    cumulative_token_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "model": None,
        "provider": None
    }

    span.set_attribute("persistent_agent.tools.count", len(tools))
    span.set_attribute("MAX_AGENT_LOOP_ITERATIONS", MAX_AGENT_LOOP_ITERATIONS)

    # Determine remaining steps from the shared budget (if any)
    budget_ctx = get_budget_context()
    eval_run_id = getattr(budget_ctx, "eval_run_id", None) if budget_ctx is not None else None
    max_remaining = MAX_AGENT_LOOP_ITERATIONS
    if budget_ctx is not None:
        steps_used = AgentBudgetManager.get_steps_used(agent_id=budget_ctx.agent_id)
        max_remaining = max(0, min(MAX_AGENT_LOOP_ITERATIONS, budget_ctx.max_steps - steps_used))
        span.set_attribute("budget.max_steps", budget_ctx.max_steps)
        span.set_attribute("budget.steps_used", steps_used)
        span.set_attribute("budget.depth", budget_ctx.depth)
        span.set_attribute("budget.max_depth", budget_ctx.max_depth)

        # If we are already out of steps before looping, close the cycle immediately
        if max_remaining == 0:
            try:
                AgentBudgetManager.close_cycle(agent_id=budget_ctx.agent_id, budget_id=budget_ctx.budget_id)
                logger.info("Agent %s step budget exhausted at entry; closing cycle.", agent.id)
            except Exception:
                logger.debug("Failed to close budget cycle at entry", exc_info=True)
            return cumulative_token_usage

    reasoning_only_streak = 0
    inferred_message_continue_streak = 0
    continuation_notice: Optional[str] = None

    for i in range(max_remaining):
        if max_runtime_seconds and _runtime_exceeded(run_started_at, max_runtime_seconds):
            logger.warning(
                "Agent %s loop aborted after %d seconds (max=%d).",
                agent.id,
                int(time.monotonic() - run_started_at),
                max_runtime_seconds,
            )
            span.add_event("Agent loop aborted - runtime limit")
            if heartbeat:
                heartbeat.touch("runtime_limit")
            try:
                PersistentAgentStep.objects.create(
                    agent=agent,
                    description=(
                        "Processing halted: runtime limit reached. "
                        "Will resume on the next trigger."
                    ),
                )
            except DatabaseError:
                logger.debug(
                    "Failed to persist runtime limit step for agent %s",
                    agent.id,
                    exc_info=True,
                )
            pending_settings = get_pending_drain_settings(settings)
            enqueue_pending_agent(
                agent.id,
                ttl=pending_settings.pending_set_ttl_seconds,
            )
            _schedule_pending_drain(
                delay_seconds=pending_settings.pending_drain_delay_seconds,
                schedule_ttl_seconds=pending_settings.pending_drain_schedule_ttl_seconds,
                span=span,
            )
            span.add_event("Runtime limit follow-up queued")
            _attempt_cycle_close_for_sleep(agent, budget_ctx)
            return cumulative_token_usage
        with tracer.start_as_current_span(f"Agent Loop Iteration {i + 1}"):
            iter_span = trace.get_current_span()
            if heartbeat:
                heartbeat.touch("iteration_start")
            if lock_extender:
                lock_extender.maybe_extend()
            try:
                daily_state = get_agent_daily_credit_state(agent)
            except Exception:
                logger.warning(
                    "Failed to refresh daily credit state for agent %s during loop; continuing without update.",
                    agent.id,
                    exc_info=True,
                )
                daily_state = credit_snapshot["daily_state"] if credit_snapshot else None

            if credit_snapshot is not None:
                credit_snapshot["daily_state"] = daily_state

            if should_pause_for_burn_rate(
                agent,
                budget_ctx=budget_ctx,
                span=iter_span,
                daily_state=daily_state,
                redis_client=redis_client,
                follow_up_task=burn_follow_up_task,
            ):
                logger.info(
                    "Agent %s paused due to burn rate; exiting loop after %d iteration(s).",
                    agent.id,
                    i + 1,
                )
                return cumulative_token_usage

            # Atomically consume one global step; exit if budget exhausted
            if budget_ctx is not None:
                consumed, new_used = AgentBudgetManager.try_consume_step(
                    agent_id=budget_ctx.agent_id, max_steps=budget_ctx.max_steps
                )
                iter_span.set_attribute("budget.consumed", consumed)
                iter_span.set_attribute("budget.steps_used", new_used)
                if not consumed:
                    logger.info("Agent %s step budget exhausted.", agent.id)
                    try:
                        AgentBudgetManager.close_cycle(agent_id=budget_ctx.agent_id, budget_id=budget_ctx.budget_id)
                    except Exception:
                        logger.debug("Failed to close budget cycle on exhaustion", exc_info=True)
                    return cumulative_token_usage

            config_snapshot = seed_sqlite_agent_config(agent)
            kanban_snapshot = seed_sqlite_kanban(agent)
            current_notice = continuation_notice
            continuation_notice = None
            history, fitted_token_count, prompt_archive_id = build_prompt_context(
                agent,
                current_iteration=i + 1,
                max_iterations=MAX_AGENT_LOOP_ITERATIONS,
                reasoning_only_streak=reasoning_only_streak,
                is_first_run=is_first_run,
                daily_credit_state=daily_state,
                continuation_notice=current_notice,
                routing_profile=get_current_eval_routing_profile(),
            )
            prompt_archive_attached = False

            def _attach_prompt_archive(step: PersistentAgentStep) -> None:
                nonlocal prompt_archive_attached
                if not prompt_archive_id or prompt_archive_attached:
                    return
                try:
                    updated = PersistentAgentPromptArchive.objects.filter(
                        id=prompt_archive_id,
                        step__isnull=True,
                    ).update(step=step)
                    if updated:
                        prompt_archive_attached = True
                except Exception:
                    logger.exception(
                        "Failed to link prompt archive %s to step %s",
                        prompt_archive_id,
                        getattr(step, "id", None),
                    )

            def _token_usage_fields(token_usage: Optional[dict], response: Any) -> dict:
                """Return sanitized token usage values for step creation."""
                return completion_kwargs_from_usage(
                    token_usage,
                    completion_type=PersistentAgentCompletion.CompletionType.ORCHESTRATOR,
                    response=response,
                )

            # Use the fitted token count from promptree for LLM selection
            # This fixes the bug where we were using joined message token count
            # which could exceed thresholds even when fitted content was under limits
            logger.debug(
                "Using fitted token count %d for agent %s LLM selection",
                fitted_token_count,
                agent.id
            )

            # Select provider tiers based on the fitted token count
            prefer_low_latency = has_active_web_session(agent)
            try:
                failover_configs = get_llm_config_with_failover(
                    agent_id=str(agent.id),
                    token_count=fitted_token_count,
                    agent=agent,
                    is_first_loop=is_first_run,
                    routing_profile=get_current_eval_routing_profile(),
                    prefer_low_latency=prefer_low_latency,
                )
            except LLMNotConfiguredError:
                logger.warning(
                    "Agent %s loop aborted – LLM configuration missing mid-run.",
                    agent.id,
                )
                span.add_event("Agent loop aborted - llm bootstrap required")
                break

            preferred_config = _get_recent_preferred_config(agent=agent, run_sequence_number=run_sequence_number)
            if prefer_low_latency:
                preferred_config = _filter_preferred_config_for_low_latency(
                    preferred_config,
                    failover_configs,
                    agent_id=str(agent.id),
                )
            stream_broadcaster = None
            try:
                stream_target = resolve_web_stream_target(agent)
                if stream_target:
                    stream_broadcaster = WebStreamBroadcaster(stream_target)
            except Exception:
                logger.debug("Failed to resolve web stream target for agent %s", agent.id, exc_info=True)

            try:
                response, token_usage = _completion_with_failover(
                    messages=history,
                    tools=tools,
                    failover_configs=failover_configs,
                    agent_id=str(agent.id),
                    safety_identifier=agent.user.id if agent.user else None,
                    preferred_config=preferred_config,
                    stream_broadcaster=stream_broadcaster,
                )
                if heartbeat:
                    heartbeat.touch("llm_response")

                # Accumulate token usage
                if token_usage:
                    cumulative_token_usage["prompt_tokens"] += token_usage.get("prompt_tokens", 0)
                    cumulative_token_usage["completion_tokens"] += token_usage.get("completion_tokens", 0)
                    cumulative_token_usage["total_tokens"] += token_usage.get("total_tokens", 0)
                    cumulative_token_usage["cached_tokens"] += token_usage.get("cached_tokens", 0)
                    # Keep the last model and provider
                    cumulative_token_usage["model"] = token_usage.get("model")
                    cumulative_token_usage["provider"] = token_usage.get("provider")
                    logger.info(
                        "LLM usage: model=%s provider=%s pt=%s ct=%s tt=%s",
                        token_usage.get("model"),
                        token_usage.get("provider"),
                        token_usage.get("prompt_tokens"),
                        token_usage.get("completion_tokens"),
                        token_usage.get("total_tokens"),
                    )

            except Exception as e:
                current_span = trace.get_current_span()
                mark_span_failed_with_exception(current_span, e, "LLM completion failed with all providers")
                logger.exception("LLM call failed for agent %s with all providers", agent.id)
                break

            thinking_content = extract_reasoning_content(response)
            msg = response.choices[0].message
            token_usage_fields = _token_usage_fields(token_usage, response)
            completion: Optional[PersistentAgentCompletion] = None

            def _ensure_completion() -> PersistentAgentCompletion:
                nonlocal completion
                if completion is None:
                    completion = PersistentAgentCompletion.objects.create(
                        agent=agent,
                        eval_run_id=eval_run_id,
                        thinking_content=thinking_content,
                        **token_usage_fields,
                    )
                return completion

            # Persist completion immediately so token usage isn't lost if execution exits early
            _ensure_completion()

            def _attach_completion(step_kwargs: dict) -> None:
                completion_obj = _ensure_completion()
                step_kwargs["completion"] = completion_obj

            def _persist_reasoning_step(reasoning_source: Optional[str]) -> None:
                reasoning_text = (reasoning_source or "").strip()
                if not reasoning_text:
                    return
                step_kwargs = {
                    "agent": agent,
                    "description": f"{INTERNAL_REASONING_PREFIX} {reasoning_text}",
                }
                _attach_completion(step_kwargs)
                step = PersistentAgentStep.objects.create(**step_kwargs)
                _attach_prompt_archive(step)

            def _apply_agent_config_updates() -> bool:
                config_apply = apply_sqlite_agent_config_updates(agent, config_snapshot)
                if not config_apply.errors:
                    return False
                for error in config_apply.errors:
                    try:
                        step_kwargs = {
                            "agent": agent,
                            "description": f"Agent config update failed: {error}",
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                    except Exception:
                        logger.debug(
                            "Failed to persist config update error step for agent %s",
                            agent.id,
                            exc_info=True,
                        )
                return True

            def _apply_kanban_updates() -> tuple[bool, Optional["KanbanBoardSnapshot"]]:
                """Apply kanban updates and return (had_errors, snapshot)."""
                from api.agent.tools.sqlite_kanban import KanbanBoardSnapshot
                kanban_apply = apply_sqlite_kanban_updates(agent, kanban_snapshot)

                # Broadcast kanban changes to timeline if any
                if kanban_apply.changes and kanban_apply.snapshot:
                    try:
                        broadcast_kanban_changes(agent, kanban_apply.changes, kanban_apply.snapshot)
                    except Exception:
                        logger.debug(
                            "Failed to broadcast kanban changes for agent %s",
                            agent.id,
                            exc_info=True,
                        )

                if not kanban_apply.errors:
                    return False, kanban_apply.snapshot
                for error in kanban_apply.errors:
                    try:
                        step_kwargs = {
                            "agent": agent,
                            "description": f"Kanban update failed: {error}",
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                    except Exception:
                        logger.debug(
                            "Failed to persist kanban update error step for agent %s",
                            agent.id,
                            exc_info=True,
                        )
                return True, kanban_apply.snapshot

            msg_content = _extract_message_content(msg)
            raw_message_text = (msg_content or "").strip()
            message_text, has_canonical_continuation = _strip_canonical_continuation_phrase(
                raw_message_text
            )

            raw_tool_calls = _normalize_tool_calls(msg)
            raw_tool_names = [_get_tool_call_name(call) for call in raw_tool_calls]
            has_explicit_send = any(name in MESSAGE_TOOL_NAMES for name in raw_tool_names if name)
            has_explicit_sleep = any(name == "sleep_until_next_trigger" for name in raw_tool_names if name)
            has_other_tool_calls = any(
                name and name != "sleep_until_next_trigger" for name in raw_tool_names
            )

            implied_send = False
            tool_calls = list(raw_tool_calls)
            implied_stop_after_send = False  # Track if implied send should force stop
            implied_send_message_text = ""
            if message_text and not has_explicit_send:
                # Default: STOP. Agent must explicitly request continuation with "CONTINUE_WORK_SIGNAL".
                # This is safer—agent won't keep running unexpectedly.
                has_natural_continuation_signal = _has_continuation_signal(raw_message_text)
                has_open_kanban_work = _has_open_kanban_work(agent)
                implied_will_continue = _should_imply_continue(
                    has_canonical_continuation=has_canonical_continuation,
                    has_other_tool_calls=has_other_tool_calls,
                    has_explicit_sleep=has_explicit_sleep,
                    has_open_kanban_work=has_open_kanban_work,
                    has_natural_continuation_signal=has_natural_continuation_signal,
                )
                if (
                    implied_will_continue
                    and has_open_kanban_work
                    and has_natural_continuation_signal
                    and not has_canonical_continuation
                    and not has_other_tool_calls
                ):
                    logger.info(
                        "Agent %s: implied send continuing due to open kanban work + continuation signal.",
                        agent.id,
                    )
                implied_call, implied_error = _build_implied_send_tool_call(
                    agent,
                    message_text,
                    will_continue_work=implied_will_continue,
                )
                if implied_call:
                    implied_send = True
                    implied_stop_after_send = not implied_will_continue  # Stop unless continuation phrase
                    implied_send_message_text = message_text
                    tool_calls = [implied_call] + tool_calls
                    logger.info(
                        "Agent %s: treating message content as implied %s send.",
                        agent.id,
                        implied_call.get("function", {}).get("name"),
                    )
                else:
                    logger.warning(
                        "Agent %s: implied send unavailable (%s)",
                        agent.id,
                        implied_error or "unknown error",
                    )
                    try:
                        step_kwargs = {
                            "agent": agent,
                            "description": (
                                "Message delivery requires explicit send tools when no active web chat session. "
                                "Please call send_chat_message for web chat, or send_email/send_sms with explicit parameters."
                            ),
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                    except Exception:
                        logger.debug("Failed to persist implied-send correction step", exc_info=True)
                    # Don't continue here - still execute any other tool calls that were returned

            reasoning_source = thinking_content
            if not reasoning_source and not implied_send:
                reasoning_source = msg_content

            _persist_reasoning_step(reasoning_source)

            if not tool_calls:
                config_errors = _apply_agent_config_updates()
                kanban_errors, _ = _apply_kanban_updates()
                if config_errors or kanban_errors:
                    reasoning_only_streak = 0
                    continue
                if not message_text and not thinking_content:
                    # Truly empty response (no text, no thinking, no tools) = agent is done
                    # Log kanban state to help diagnose premature termination
                    kanban_state = "unknown"
                    try:
                        from .prompt_context import get_kanban_snapshot
                        snap = get_kanban_snapshot(agent)
                        if snap:
                            kanban_state = f"todo={snap.todo_count}, doing={snap.doing_count}, done={snap.done_count}"
                    except Exception:
                        pass
                    logger.info(
                        "Agent %s: empty response (no message, no thinking, no tools), auto-sleeping. "
                        "Kanban at termination: %s. Raw msg_content type=%s, len=%s",
                        agent.id,
                        kanban_state,
                        type(msg_content).__name__,
                        len(msg_content) if msg_content else 0,
                    )
                    _attempt_cycle_close_for_sleep(agent, budget_ctx)
                    return cumulative_token_usage
                # Message or thinking content but no tools - increment streak.
                # Thinking-only models (e.g., DeepSeek) put responses in thinking blocks;
                # don't auto-sleep just because message_text is empty.
                reasoning_only_streak += 1

                # Check for continuation signals like "let me", "I'll", "I'm going to"
                # in message or thinking content - gives agent one extra pass.
                has_continuation = _has_continuation_signal(raw_message_text) or _has_continuation_signal(thinking_content or "")
                effective_limit = MAX_NO_TOOL_STREAK + 1 if has_continuation else MAX_NO_TOOL_STREAK

                if reasoning_only_streak >= effective_limit:
                    # Log kanban state to help diagnose premature termination
                    kanban_state = "unknown"
                    try:
                        from .prompt_context import get_kanban_snapshot
                        snap = get_kanban_snapshot(agent)
                        if snap:
                            kanban_state = f"todo={snap.todo_count}, doing={snap.doing_count}, done={snap.done_count}"
                            if snap.todo_count > 0 or snap.doing_count > 0:
                                logger.warning(
                                    "Agent %s: auto-sleeping with unfinished kanban work! %s",
                                    agent.id,
                                    kanban_state,
                                )
                    except Exception:
                        pass
                    logger.info(
                        "Agent %s: %d consecutive responses without tool calls (limit=%d), auto-sleeping. "
                        "Kanban: %s. Last message preview: %.100s",
                        agent.id,
                        reasoning_only_streak,
                        effective_limit,
                        kanban_state,
                        message_text or thinking_content or "(none)",
                    )
                    _attempt_cycle_close_for_sleep(agent, budget_ctx)
                    return cumulative_token_usage
                continue

            reasoning_only_streak = 0

            # Log high-level summary of tool calls
            try:
                logger.info(
                    "Agent %s: model returned %d tool_call(s)",
                    agent.id,
                    len(tool_calls) if isinstance(tool_calls, list) else 0,
                )
                for idx, call in enumerate(list(tool_calls) or [], start=1):
                    try:
                        fn_name = _get_tool_call_name(call)
                        raw_args = _get_tool_call_arguments(call) or ""
                        call_id = getattr(call, "id", None) or (call.get("id") if isinstance(call, dict) else None)
                        arg_preview = (raw_args or "")[:ARG_LOG_MAX_CHARS]
                        logger.info(
                            "Agent %s: tool_call %d: id=%s name=%s args=%s%s",
                            agent.id,
                            idx,
                            call_id or "<none>",
                            fn_name or "<unknown>",
                            arg_preview,
                            "…" if raw_args and len(raw_args) > len(arg_preview) else "",
                        )
                    except Exception:
                        logger.info("Agent %s: failed to log one tool_call entry", agent.id)
            except Exception:
                logger.debug("Tool call summary logging failed", exc_info=True)

            executed_calls = 0
            followup_required = False
            last_explicit_continue: Optional[bool] = None  # Final explicit will_continue_work in batch
            allow_inferred_message_continue = inferred_message_continue_streak == 0
            inferred_continue_call_tokens: set[str] = set()
            inferred_message_continue_this_iteration = False
            executed_non_message_action = False
            try:
                tool_names = [_get_tool_call_name(c) for c in (tool_calls or [])]
                has_non_sleep_calls = any(name != "sleep_until_next_trigger" for name in tool_names)
                actionable_calls_total = sum(
                    1 for name in tool_names if name != "sleep_until_next_trigger"
                )
                has_user_facing_message = any(
                    name in MESSAGE_TOOL_NAMES for name in tool_names if name
                )
            except Exception:
                # Defensive fallback: assume we have actionable work so the agent keeps processing
                has_non_sleep_calls = True
                actionable_calls_total = len(tool_calls or []) if tool_calls else 0
                has_user_facing_message = False
            message_delivery_ok = False
            all_calls_sleep = not has_non_sleep_calls

            for idx, call in enumerate(tool_calls, start=1):
                with tracer.start_as_current_span("Execute Tool") as tool_span:
                    if lock_extender:
                        lock_extender.maybe_extend()
                    tool_span.set_attribute("persistent_agent.id", str(agent.id))
                    tool_name = _get_tool_call_name(call)
                    if not tool_name:
                        logger.warning(
                            "Agent %s: received tool call without a function name; skipping and requesting resend.",
                            agent.id,
                        )
                        try:
                            step_kwargs = {
                                "agent": agent,
                                "description": (
                                    "Tool call error: missing function name. "
                                    "Re-send the SAME tool call with a valid 'name' and JSON arguments."
                                ),
                            }
                            _attach_completion(step_kwargs)
                            step = PersistentAgentStep.objects.create(**step_kwargs)
                            _attach_prompt_archive(step)
                            logger.info(
                                "Agent %s: added correction step_id=%s for missing tool name",
                                agent.id,
                                getattr(step, "id", None),
                            )
                        except Exception:
                            logger.debug("Failed to persist correction step for missing tool name", exc_info=True)
                        followup_required = True
                        break
                    if heartbeat:
                        heartbeat.touch("tool_call")
                    tool_span.set_attribute("tool.name", tool_name)
                    logger.info("Agent %s executing tool %d/%d: %s", agent.id, idx, len(tool_calls), tool_name)

                    if tool_name == "sleep_until_next_trigger":
                        # Ignore sleep tool if there are other actionable tools in this batch
                        if has_non_sleep_calls:
                            logger.info(
                                "Agent %s: ignoring sleep_until_next_trigger because other tools are present in this batch.",
                                agent.id,
                            )
                            # Do not consume credits or record a step for ignored sleep
                            continue
                        # All tool calls are sleep; consume credits once per call and record step
                        credit_info = _ensure_credit_for_tool(
                            agent,
                            tool_name,
                            span=tool_span,
                            credit_snapshot=credit_snapshot,
                        )
                        if not credit_info:
                            return cumulative_token_usage
                        credits_consumed = credit_info.get("cost")
                        consumed_credit = credit_info.get("credit")
                        # Create sleep step with token usage if available
                        step_kwargs = {
                            "agent": agent,
                            "description": "Decided to sleep until next trigger.",
                            "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
                            "task_credit": consumed_credit,
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                        logger.info("Agent %s: sleep_until_next_trigger recorded (will sleep after batch)", agent.id)
                        continue

                    all_calls_sleep = False
                    if not _enforce_tool_rate_limit(
                        agent,
                        tool_name,
                        span=tool_span,
                        attach_completion=_attach_completion,
                        attach_prompt_archive=_attach_prompt_archive,
                    ):
                        followup_required = True
                        continue

                    # Ensure credit is available and consume just-in-time for actionable tools
                    credit_info = _ensure_credit_for_tool(
                        agent,
                        tool_name,
                        span=tool_span,
                        credit_snapshot=credit_snapshot,
                    )
                    if not credit_info:
                        # Credit insufficient or consumption failed; halt processing
                        return cumulative_token_usage
                    credits_consumed = credit_info.get("cost")
                    consumed_credit = credit_info.get("credit")
                    try:
                        raw_args = _get_tool_call_arguments(call)
                        if isinstance(raw_args, dict):
                            tool_params = raw_args
                            raw_args = json.dumps(raw_args)
                        else:
                            raw_args = raw_args or ""
                            tool_params = json.loads(raw_args)
                    except Exception:
                        # Simple recovery: record a correction instruction and retry next iteration.
                        preview = (raw_args or "")[:ARG_LOG_MAX_CHARS]
                        logger.warning(
                            "Agent %s: invalid JSON for tool %s; prompting model to resend valid arguments (preview=%s%s)",
                            agent.id,
                            tool_name,
                            preview,
                            "…" if raw_args and len(raw_args) > len(preview) else "",
                        )
                        try:
                            step_text = (
                                f"Tool call error: arguments for {tool_name} were not valid JSON. "
                                "Re-send the SAME tool call immediately with valid JSON only. "
                                "For HTML content, use single quotes for all attributes to avoid JSON conflicts."
                            )
                            step_kwargs = {
                                "agent": agent,
                                "description": step_text,
                                "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
                                "task_credit": consumed_credit,
                            }
                            _attach_completion(step_kwargs)
                            step = PersistentAgentStep.objects.create(**step_kwargs)
                            _attach_prompt_archive(step)
                            logger.info(
                                "Agent %s: added correction step_id=%s to request a retried tool call",
                                agent.id,
                                getattr(step, 'id', None),
                            )
                        except Exception:
                            logger.debug("Failed to persist correction step", exc_info=True)
                        # Abort remaining tool calls this iteration; retry next loop
                        followup_required = True
                        break
                    call_id = getattr(call, "id", None)
                    if not call_id and isinstance(call, dict):
                        call_id = call.get("id")
                    call_token = str(call_id or f"idx-{idx}")
                    if tool_name in MESSAGE_TOOL_NAMES:
                        body_key = MESSAGE_TOOL_BODY_KEYS.get(tool_name)
                        explicit_continue_hint = _coerce_optional_bool(tool_params.get("will_continue_work"))
                        if body_key and isinstance(tool_params.get(body_key), str):
                            cleaned_body, found_phrase = _strip_canonical_continuation_phrase(
                                tool_params[body_key]
                            )
                            if found_phrase:
                                tool_params[body_key] = cleaned_body
                                tool_params["will_continue_work"] = True
                            elif (
                                explicit_continue_hint is None
                                and allow_inferred_message_continue
                                and _should_infer_message_tool_continuation(cleaned_body)
                            ):
                                tool_params["will_continue_work"] = True
                                inferred_continue_call_tokens.add(call_token)
                                logger.info(
                                    "Agent %s: inferred will_continue_work=true for %s based on progress-update language.",
                                    agent.id,
                                    tool_name,
                                )
                            elif (
                                explicit_continue_hint is None
                                and not allow_inferred_message_continue
                                and _should_infer_message_tool_continuation(cleaned_body)
                            ):
                                logger.info(
                                    "Agent %s: suppressing inferred continuation for %s to avoid progress-message loops without work tools.",
                                    agent.id,
                                    tool_name,
                                )
                        # Explicit message tools still default to STOP when the flag is omitted.
                        # We only auto-continue on strong progress-update language.
                    tool_span.set_attribute("tool.params", json.dumps(tool_params))
                    logger.info("Agent %s: %s params=%s", agent.id, tool_name, json.dumps(tool_params)[:ARG_LOG_MAX_CHARS])

                    # Substitute $[var] placeholders in tool parameters (unless tool opts out)
                    if should_skip_auto_substitution(tool_name):
                        exec_params = tool_params  # Tool handles substitution itself
                    else:
                        exec_params = _substitute_variables_in_params(tool_params)
                    if tool_name == "sqlite_batch":
                        exec_params = dict(exec_params)  # copy already-substituted params
                        exec_params["_has_user_facing_message"] = has_user_facing_message

                    # Ensure a fresh DB connection before tool execution and subsequent ORM writes
                    close_old_connections()

                    pending_step = _create_pending_tool_call_step(
                        agent=agent,
                        tool_name=tool_name,
                        tool_params=tool_params,
                        credits_consumed=credits_consumed,
                        consumed_credit=consumed_credit,
                        attach_completion=_attach_completion,
                        attach_prompt_archive=_attach_prompt_archive,
                    )

                    logger.info("Agent %s: executing %s now", agent.id, tool_name)

                    tool_started_at = time.monotonic()
                    tool_duration_ms: Optional[int] = None
                    try:
                        # Check for eval mock before real execution (mock_config passed via BudgetContext)
                        mock_config = getattr(budget_ctx, "mock_config", None) if budget_ctx else None
                        mock_result = mock_config.get(tool_name) if mock_config else None
                        if mock_result is not None:
                            logger.info(
                                "Agent %s: using mock for %s (eval_run_id=%s)",
                                agent.id,
                                tool_name,
                                eval_run_id,
                            )
                            result = mock_result
                        elif tool_name == "spawn_web_task":
                            # Delegate recursion gating to execute_spawn_web_task which reads fresh branch depth from Redis
                            result = execute_spawn_web_task(agent, exec_params)
                        elif tool_name == "send_email":
                            result = execute_send_email(agent, exec_params)
                        elif tool_name == "send_sms":
                            result = execute_send_sms(agent, exec_params)
                        elif tool_name == "send_chat_message":
                            result = execute_send_chat_message(agent, exec_params)
                        elif tool_name == "send_agent_message":
                            result = execute_send_agent_message(agent, exec_params)
                        elif tool_name == "send_webhook_event":
                            result = execute_send_webhook_event(agent, exec_params)
                        elif tool_name == "update_schedule":
                            result = execute_update_schedule(agent, exec_params)
                        elif tool_name == "update_charter":
                            result = execute_update_charter(agent, exec_params)
                        elif tool_name == "secure_credentials_request":
                            result = execute_secure_credentials_request(agent, exec_params)
                        elif tool_name == "enable_database":
                            result = execute_enable_database(agent, exec_params)
                            before_count = len(tools)
                            tools = get_agent_tools(agent)
                            after_count = len(tools)
                            logger.info(
                                "Agent %s: refreshed tools after enable_database (before=%d after=%d)",
                                agent.id,
                                before_count,
                                after_count,
                            )
                        elif tool_name == "request_contact_permission":
                            result = execute_request_contact_permission(agent, exec_params)
                        elif tool_name == "search_tools":
                            result = execute_search_tools(agent, exec_params)
                            # After search_tools auto-enables relevant tools, refresh tool definitions
                            before_count = len(tools)
                            tools = get_agent_tools(agent)
                            after_count = len(tools)
                            logger.info(
                                "Agent %s: refreshed tools after search_tools (before=%d after=%d)",
                                agent.id,
                                before_count,
                                after_count,
                            )
                        else:
                            # 'enable_tool' is no longer exposed to the main agent; enabling is handled internally by search_tools
                            result = execute_enabled_tool(agent, tool_name, exec_params)
                    except Exception as exc:
                        mark_span_failed_with_exception(tool_span, exc, f"Tool {tool_name} failed")
                        logger.exception(
                            "Agent %s: tool %s failed (call_id=%s)",
                            agent.id,
                            tool_name,
                            call_id or "<none>",
                        )
                        result = _build_safe_error_payload(
                            f"Tool execution failed: {exc}",
                            error_type=type(exc).__name__,
                            retryable=_infer_retryable_from_text(str(exc)),
                        )
                    finally:
                        tool_duration_ms = int(round((time.monotonic() - tool_started_at) * 1000))

                    if _is_error_status(result):
                        result = _normalize_error_result(result)

                    try:
                        result_content = json.dumps(result)
                    except (TypeError, ValueError):
                        try:
                            result_content = json.dumps(result, default=str)
                        except Exception as exc:
                            logger.exception(
                                "Agent %s: failed to serialize tool result for %s (call_id=%s)",
                                agent.id,
                                tool_name,
                                call_id or "<none>",
                            )
                            result = _build_safe_error_payload(
                                "Tool result serialization failed.",
                                error_type=type(exc).__name__,
                                retryable=False,
                            )
                            result_content = json.dumps(result)
                    # Log result summary
                    try:
                        status = result.get("status") if isinstance(result, dict) else None
                    except Exception:
                        status = None
                    try:
                        tool_span.set_attribute("tool.status", str(status or ""))
                    except Exception:
                        pass
                    result_preview = result_content[:RESULT_LOG_MAX_CHARS]
                    logger.info(
                        "Agent %s: %s completed status=%s result=%s%s",
                        agent.id,
                        tool_name,
                        status or "",
                        result_preview,
                        "…" if len(result_content) > len(result_preview) else "",
                    )
                    if tool_name in MESSAGE_TOOL_NAMES:
                        status_label = str(status or "").lower()
                        if status_label in MESSAGE_SUCCESS_STATUSES:
                            message_delivery_ok = True

                    is_error_status = _is_error_status(result)
                    tool_status = "error" if is_error_status else "complete"

                    # Guard ORM writes against stale connections; retry once on OperationalError
                    close_old_connections()
                    if pending_step is not None:
                        _finalize_pending_tool_call_step(
                            step=pending_step,
                            tool_name=tool_name,
                            tool_params=tool_params,
                            result_content=result_content,
                            execution_duration_ms=tool_duration_ms,
                            status=tool_status,
                        )
                        step = pending_step
                    else:
                        step = _persist_tool_call_step(
                            agent=agent,
                            tool_name=tool_name,
                            tool_params=tool_params,
                            result_content=result_content,
                            execution_duration_ms=tool_duration_ms,
                            status=tool_status,
                            credits_consumed=credits_consumed,
                            consumed_credit=consumed_credit,
                            attach_completion=_attach_completion,
                            attach_prompt_archive=_attach_prompt_archive,
                        )
                    allow_auto_sleep = isinstance(result, dict) and result.get(AUTO_SLEEP_FLAG) is True
                    tool_had_error = is_error_status
                    tool_had_warning = _is_warning_status(result)
                    explicit_continue = None
                    if isinstance(tool_params, dict):
                        explicit_continue = _coerce_optional_bool(tool_params.get("will_continue_work"))
                        if explicit_continue is not None:
                            last_explicit_continue = explicit_continue
                        if explicit_continue is True and call_token in inferred_continue_call_tokens:
                            inferred_message_continue_this_iteration = True

                    if tool_had_error or tool_had_warning:
                        followup_required = True
                    elif explicit_continue is None and not allow_auto_sleep:
                        followup_required = True

                    executed_calls += 1
                    if tool_name not in MESSAGE_TOOL_NAMES and tool_name != "sleep_until_next_trigger":
                        executed_non_message_action = True

            config_had_errors = _apply_agent_config_updates()
            kanban_had_errors, _ = _apply_kanban_updates()
            if config_had_errors or kanban_had_errors:
                followup_required = True

            if executed_non_message_action:
                inferred_message_continue_streak = 0
            elif inferred_message_continue_this_iteration:
                inferred_message_continue_streak += 1
            else:
                inferred_message_continue_streak = 0

            if all_calls_sleep:
                logger.info("Agent %s is sleeping.", agent.id)
                _attempt_cycle_close_for_sleep(agent, budget_ctx)
                return cumulative_token_usage
            elif not followup_required and last_explicit_continue is False:
                logger.info(
                    "Agent %s: tool batch ended with explicit stop; auto-sleeping.",
                    agent.id,
                )
                _attempt_cycle_close_for_sleep(agent, budget_ctx)
                return cumulative_token_usage
            # Implied send without continuation phrase = agent is done, force stop
            elif (
                implied_stop_after_send
                and message_delivery_ok
                and not followup_required
                and last_explicit_continue is None
            ):
                # Re-check against persisted kanban/message state before stopping.
                # This prevents premature sleep when initial implied-continuation inference
                # was too conservative but the delivered text clearly signals ongoing work.
                if (
                    implied_send_message_text
                    and _has_open_kanban_work(agent)
                    and _has_continuation_signal(implied_send_message_text)
                ):
                    logger.info(
                        "Agent %s: implied send stop overridden due to open kanban work + continuation signal.",
                        agent.id,
                    )
                    continue
                logger.info(
                    "Agent %s: implied send without continuation phrase; auto-sleeping.",
                    agent.id,
                )
                _attempt_cycle_close_for_sleep(agent, budget_ctx)
                return cumulative_token_usage
            elif (
                not followup_required
                and last_explicit_continue is None
                and executed_calls > 0
                and executed_calls >= actionable_calls_total
            ):
                logger.info(
                    "Agent %s: tool batch complete with no follow-up required; auto-sleeping.",
                    agent.id,
                )
                _attempt_cycle_close_for_sleep(agent, budget_ctx)
                return cumulative_token_usage
            elif not followup_required and last_explicit_continue is True:
                logger.info(
                    "Agent %s: tools returned auto_sleep_ok but agent explicitly requested continuation; continuing.",
                    agent.id,
                )
            else:
                logger.info(
                    "Agent %s: executed %d/%d tool_call(s) this iteration",
                    agent.id,
                    executed_calls,
                    len(tool_calls),
                )

    else:
        logger.warning("Agent %s reached max iterations.", agent.id)
        span.add_event("Agent loop aborted - max iterations")
        if heartbeat:
            heartbeat.touch("max_iterations")
        try:
            PersistentAgentStep.objects.create(
                agent=agent,
                description=(
                    "Processing paused: max iterations reached. "
                    "Will resume shortly."
                ),
            )
        except DatabaseError:
            logger.debug(
                "Failed to persist max-iterations step for agent %s",
                agent.id,
                exc_info=True,
            )
        pending_settings = get_pending_drain_settings(settings)
        delay_seconds = max(
            int(MAX_ITERATIONS_FOLLOWUP_DELAY_SECONDS),
            int(pending_settings.pending_drain_delay_seconds),
        )
        try:
            from ..tasks.process_events import process_agent_events_task  # noqa: WPS433 (runtime import)

            # Avoid globally throttling the pending-drain scheduler when a single agent
            # hits its iteration cap; resume this agent directly after a cooldown.
            process_agent_events_task.apply_async(
                args=[str(agent.id)],
                countdown=delay_seconds,
            )
            span.add_event("Max iterations follow-up scheduled")
        except Exception:
            logger.debug(
                "Failed to schedule max-iterations follow-up for agent %s",
                agent.id,
                exc_info=True,
            )
        _attempt_cycle_close_for_sleep(agent, budget_ctx)

    return cumulative_token_usage
