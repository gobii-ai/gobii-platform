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
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable, List, Tuple, Union, Optional, Dict, Any, Literal
from urllib.parse import unquote_plus
from uuid import UUID

import sqlparse
from opentelemetry import baggage, trace
from pottery import Redlock
from pottery.exceptions import ExtendUnlockedLock, TooManyExtensions
from django.apps import apps
from django.conf import settings as django_settings
from django.db import DatabaseError, transaction, close_old_connections
from django.db.utils import OperationalError
from django.utils import timezone as dj_timezone
from waffle import switch_is_active

from observability import mark_span_failed_with_exception
from .budget import (
    AgentBudgetManager,
    BudgetContext,
    get_current_context as get_budget_context,
    set_current_context as set_budget_context,
)
from .burn_control import (
    BurnRateAction,
    handle_burn_rate_limit,
)
from .processing_flags import (
    clear_processing_lock_active,
    claim_pending_drain_slot,
    clear_processing_heartbeat,
    clear_processing_queued_flag,
    clear_processing_stop_requested,
    clear_processing_work_state,
    enqueue_pending_agent,
    get_human_inbound_generation,
    get_pending_drain_settings,
    is_human_inbound_generation_consumed,
    is_agent_pending,
    is_processing_queued,
    is_processing_stop_requested,
    mark_human_inbound_generation_consumed,
    mark_processing_lock_active,
    processing_lock_storage_keys,
    remove_pending_agent,
    set_processing_heartbeat,
)
from .llm_utils import (
    EmptyLiteLLMResponseError,
    raise_if_empty_litellm_response,
    raise_if_invalid_litellm_response,
    run_completion,
)
from .multimodal_context import (
    collect_fresh_read_file_image_attachments,
    prepare_multimodal_read_file_request,
)
from .llm_streaming import StreamAccumulator
from .token_usage import (
    completion_kwargs_from_usage,
    extract_reasoning_content,
    extract_token_usage,
    set_usage_span_attributes,
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
    should_refund_tool_credit_on_error,
)
from util.constants.task_constants import TASKS_UNLIMITED
from .llm_config import (
    apply_tier_credit_multiplier,
    clear_runtime_tier_override,
    get_llm_config_with_failover,
    LLMNotConfiguredError,
    is_llm_bootstrap_required,
)
from api.agent.events import publish_agent_event, AgentEventType
from api.evals.credit_policy import is_eval_credit_exempt_context
from api.evals.execution import get_current_eval_routing_profile
from . import internal_reasoning
from .daily_limit_mode import (
    DAILY_LIMIT_MESSAGE_TOOL_NAMES,
    filter_tools_for_daily_limit_message_only_mode,
    is_daily_hard_limit_message_only_mode,
    is_daily_limit_message_tool,
)
from .agent_judge import maybe_run_agent_judge
from .prompt_context import (
    build_prompt_context,
    get_agent_daily_credit_state,
    get_agent_tools,
)

from ..tools.email_sender import execute_send_email
from ..tools.sms_sender import execute_send_sms
from ..tools.spawn_web_task import execute_spawn_web_task
from ..tools.schedule_updater import execute_update_schedule
from ..tools.charter_updater import execute_update_charter
from ..tools.sqlite_agent_config import (
    apply_sqlite_agent_config_updates,
    seed_sqlite_agent_config,
)
from ..tools.sqlite_skills import apply_sqlite_skill_updates, refresh_skills_for_tool, seed_sqlite_skills
from ..tools.custom_tools import execute_create_custom_tool
from ..tools.custom_tool_names import CREATE_CUSTOM_TOOL_NAME
from ..tools.file_str_replace import execute_file_str_replace
from ..tools.plan import build_plan_snapshot, build_redundant_research_plan_skip_result, execute_update_plan
from ..tools.planning import execute_end_planning
from ..tools.runtime_execution_context import tool_execution_context
from ..tools.sqlite_state import agent_sqlite_db, get_sqlite_db_path
from ..tools.secure_credentials_request import execute_secure_credentials_request
from ..tools.request_contact_permission import execute_request_contact_permission
from ..tools.request_human_input import execute_request_human_input
from ..tools.spawn_agent import execute_spawn_agent
from ..tools.search_tools import execute_search_tools
from ..tools.static_tools import planning_mode_disallows_tool
from ..tools.tool_manager import (
    execute_enabled_tool,
    auto_enable_heuristic_tools,
    get_parallel_safe_tool_rejection_reason,
    resolve_tool_entry,
    should_skip_auto_substitution,
)
from ...services.tool_blacklist import is_tool_blacklisted_for_agent, tool_blacklist_error
from ..tools.web_chat_sender import execute_send_chat_message, has_other_contact_channel
from ..tools.peer_dm import execute_send_agent_message
from ..tools.webhook_sender import execute_send_webhook_event
from ..tools.agent_variables import (
    clear_variables,
    get_all_variables,
    replace_all_variables,
    substitute_variables,
)
from ..tools.file_export_helpers import resolve_export_target
from ..files.filespace_service import _normalize_write_path
from ..comms.human_input_requests import (
    attach_originating_step_from_result,
)
from ...models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentCompletion,
    PersistentAgentError,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    PersistentAgentPromptArchive,
    SmsContactPurpose,
)
from api.services.tool_settings import get_tool_settings_for_owner
from api.services.system_settings import get_max_parallel_tool_calls
from api.services.agent_error_logging import (
    log_agent_error,
    log_credit_failure,
    log_prompt_construction_error,
    log_tool_persistence_error,
)
from api.services.billing_snapshot import get_billing_snapshot_for_owner
from api.services.owner_execution_pause import (
    EXECUTION_PAUSE_MESSAGE,
    EXECUTION_PAUSE_NOTE,
    get_owner_execution_pause_state,
    resolve_agent_owner,
)
from api.services.signup_preview import (
    can_bypass_task_credit_for_signup_preview,
    is_signup_preview_processing_paused,
)
from api.services.web_sessions import (
    get_deliverable_web_sessions,
    has_deliverable_web_session,
)
from constants.feature_flags import AGENT_RETRY_COMPLETION_ON_WEB_SESSION_ACTIVATION
from config import settings
from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from .web_streaming import WebStreamBroadcaster, resolve_web_stream_target

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


MAX_AGENT_LOOP_ITERATIONS = 100
MAX_NO_TOOL_STREAK = 5  # Allow short reasoning-only streaks before auto-sleeping
MAX_ITERATIONS_FOLLOWUP_DELAY_SECONDS = 60
ARG_LOG_MAX_CHARS = 500
RESULT_LOG_MAX_CHARS = 500
AUTO_SLEEP_FLAG = "auto_sleep_ok"
TOOL_ERROR_MESSAGE_MAX_BYTES = 800
TOOL_ERROR_DETAIL_MAX_BYTES = 1500
_EMAIL_ADDRESS_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_E164_PHONE_CANDIDATE_RE = re.compile(r"\+\d[\d\s().-]{6,}\d")
_CONTACT_APPROVAL_TERMS = ("do you want", "want me", "should i", "may i", "can i", "ok to", "okay to", "permission", "approve", "confirm", "authorize")
_CONTACT_SEND_TERMS = ("text", "sms", "email", "e-mail", "message", "contact")
TOOL_ERROR_TYPE_MAX_BYTES = 120
PREFERRED_PROVIDER_MAX_AGE = timedelta(hours=1)
MESSAGE_TOOL_NAMES = set(DAILY_LIMIT_MESSAGE_TOOL_NAMES)
MESSAGE_SUCCESS_STATUSES = {"ok", "queued", "sent", "success"}
MESSAGE_TOOL_BODY_KEYS = {
    "send_email": "mobile_first_html",
    "send_sms": "body",
    "send_chat_message": "body",
    "send_agent_message": "message",
}
SQLITE_MUTATION_RE = re.compile(r"\b(?:insert|update|delete|replace|alter|drop|create)\b", re.IGNORECASE)
AGENT_CONFIG_TABLE_RE = re.compile(r"\b__agent_config\b", re.IGNORECASE)
DURABLE_CONFIG_INTENT_RE = re.compile(
    r"\b(?:going forward|from now on|in the future|next time|always|never|remember|prefer|preference|"
    r"update (?:your )?(?:charter|schedule|instructions)|change (?:your )?(?:charter|schedule|instructions)|"
    r"charter|schedule|scheduled|recurring|ongoing|proactive|monitor|track|alert|digest|cadence|"
    r"setup|set up|role|scope|process|workflow|customer context|client context|operating boundary)\b",
    re.IGNORECASE,
)
TRANSIENT_CONFIG_SCOPE_RE = re.compile(
    r"\b(?:for this (?:answer|response|report|task|time)|this time|just this once|today only|for now)\b",
    re.IGNORECASE,
)
STRONG_DURABLE_CONFIG_INTENT_RE = re.compile(
    r"\b(?:going forward|from now on|in the future|next time|always|never|remember|"
    r"update (?:your )?(?:charter|schedule|instructions)|change (?:your )?(?:charter|schedule|instructions)|"
    r"charter|schedule|scheduled|recurring|ongoing|proactive|monitor|track|alert|digest|cadence|"
    r"setup|set up|role|scope|process|workflow|customer context|client context|operating boundary)\b",
    re.IGNORECASE,
)
ONE_OFF_TASK_RE = re.compile(
    r"\b(?:what is|what's|who is|tell me|look up|lookup|find|fetch|get|show|give me|summari[sz]e|"
    r"report|latest|current|today|now|price|status|news|funding|one[- ](?:off|shot))\b",
    re.IGNORECASE,
)
PLANNING_EXECUTE_NOW_RE = re.compile(
    r"\b(?:do not ask questions|don't ask questions|just execute(?: now)?|execute now|just run(?: it| this)?|run (?:it|this) now|start (?:it|this|the task) now|go ahead and (?:run|execute|start|do)|just do (?:it|this|the task)|do (?:it|this|the task) now)\b",
    re.IGNORECASE,
)
PLANNING_READY_WITHOUT_GATE_RE = re.compile(r"\b(?:plan(?:'s| is) clear|scope(?:'s| is) clear|task(?:'s| is) clear|lock it in|get (?:this )?rolling)\b", re.IGNORECASE)
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

BLOCKING_HUMAN_INPUT_PATTERNS = (
    re.compile(r"\bbefore\s+(?:i|we)\b", re.IGNORECASE),
    re.compile(r"\bi\s+need\s+to\s+know\b", re.IGNORECASE),
    re.compile(r"\bi\s+need\b.*\b(?:first|before|from you)\b", re.IGNORECASE),
    re.compile(r"\b(?:please|can you|could you)\s+(?:clarify|provide|share|confirm|choose|tell|send|point|direct|link)\b", re.IGNORECASE),
    re.compile(r"\bwhich\b.*\bshould\s+(?:i|we)\b", re.IGNORECASE),
    re.compile(r"\bwhat\b.*\bshould\s+(?:i|we)\b", re.IGNORECASE),
    re.compile(r"\b(?:which|what)\b.*\bwould\s+you\s+like\s+(?:me|us)\b", re.IGNORECASE),
)
MARKDOWN_PUNCTUATION_RE = re.compile(r"[*_`>#\[\]]+")
OPTIONAL_NON_BLOCKING_QUESTION_RE = re.compile(
    r"\b(?:any tweaks|any changes|anything to adjust|otherwise\b|if not\b|unless you want)\b",
    re.IGNORECASE,
)


class OrchestratorPromptStale(RuntimeError):
    """Raised when newer human input makes an in-flight orchestrator prompt stale."""


def _looks_like_blocking_human_input_request(message_text: str) -> bool:
    tableless_text = "\n".join(
        line
        for line in (message_text or "").splitlines()
        if not line.lstrip().startswith("|")
    )
    if "?" not in tableless_text:
        return False
    if len(message_text or "") > 800 and (
        "##" in message_text
        or "###" in message_text
        or "Sources" in message_text
        or "|" in message_text
    ):
        return False

    normalized = " ".join(tableless_text.split())
    if OPTIONAL_NON_BLOCKING_QUESTION_RE.search(normalized):
        return False
    return bool("?" in normalized and any(pattern.search(normalized) for pattern in BLOCKING_HUMAN_INPUT_PATTERNS))


def _extract_human_input_question(message_text: str) -> str:
    for raw_line in (message_text or "").splitlines():
        line = MARKDOWN_PUNCTUATION_RE.sub("", raw_line).strip(" -\t")
        if "?" in line:
            return _truncate_text_bytes(line, 500).strip()

    fallback = MARKDOWN_PUNCTUATION_RE.sub("", message_text or "").strip()
    return _truncate_text_bytes(fallback, 500).strip()


def _request_human_input_params_from_blocking_chat_question(
    agent: PersistentAgent,
    message_text: str,
    original_tool_params: Dict[str, Any],
) -> Dict[str, Any]:
    params = {
        "question": _extract_human_input_question(message_text),
        "will_continue_work": _coerce_optional_bool(original_tool_params.get("will_continue_work")) is True,
    }
    if agent.planning_state == PersistentAgent.PlanningState.PLANNING:
        params["options"] = [
            {
                "label": "I'll provide details",
                "description": "Share the missing planning detail before the agent continues.",
            },
            {
                "label": "Use a default",
                "description": "Let the agent choose a reasonable default and continue.",
            },
            {
                "label": "Other",
                "description": "Explain a different preference or constraint.",
            },
        ]
    return params


def _latest_inbound_message_text(agent: PersistentAgent) -> str:
    message = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=False)
        .order_by("-timestamp", "-seq")
        .values_list("body", flat=True)
        .first()
    )
    return str(message or "")


def _user_asked_for_setup_question(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    if not lower:
        return False
    return (
        lower.startswith(("ask ", "ask me ", "ask which ", "ask what "))
        or "before setting up" in lower
        or "before starting" in lower
        or "before you start" in lower
        or "ask me which" in lower
        or "ask which" in lower
    )


def _looks_like_defaultable_recurring_setup_request(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    if not lower or _user_asked_for_setup_question(lower):
        return False

    has_setup_action = any(
        term in lower
        for term in (
            "set ",
            "set up",
            "schedule",
            "monitor",
            "track",
            "check",
            "alert",
            "digest",
            "report",
        )
    )
    has_recurring_signal = any(
        term in lower
        for term in (
            "schedule",
            "daily",
            "hourly",
            "weekly",
            "weekday",
            "weekdays",
            "every ",
            "recurring",
            "regular",
            "monitor",
            "digest",
            "alert",
        )
    )
    has_config_target = any(
        term in lower
        for term in (
            "schedule",
            "monitor",
            "digest",
            "alert",
            "report",
            "check",
            "track",
        )
    )
    return has_setup_action and has_recurring_signal and has_config_target


def _looks_like_defaultable_setup_question(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    if not lower:
        return False
    return any(
        term in lower
        for term in (
            "which competitors",
            "what competitors",
            "which products",
            "what products",
            "which vendors",
            "what vendors",
            "what types of updates",
            "how often should",
            "what cadence",
            "specific data sources",
            "which data sources",
            "where should i send",
            "where should this",
            "what details",
            "need a few details",
            "need a few specifics",
            "need to know",
            "need details",
            "need specifics",
            "to make it work",
            "to make the digest useful",
            "before i configure",
            "before setting",
        )
    )


def _request_human_input_question_texts(tool_params: dict[str, Any]) -> list[str]:
    texts = [str(tool_params.get("question") or "")]
    for raw_requests in (tool_params.get("requests"), tool_params.get("questions")):
        if isinstance(raw_requests, list):
            for request in raw_requests:
                if isinstance(request, dict):
                    texts.append(str(request.get("question") or ""))
        elif isinstance(raw_requests, dict):
            texts.append(str(raw_requests.get("question") or ""))
    return [text for text in texts if text.strip()]


def _should_reject_defaultable_setup_question(agent: PersistentAgent, question_text: str) -> bool:
    if agent.planning_state == PersistentAgent.PlanningState.PLANNING:
        return False
    latest_user_text = _latest_inbound_message_text(agent)
    return _looks_like_defaultable_recurring_setup_request(
        latest_user_text
    ) and _looks_like_defaultable_setup_question(question_text)


def _record_defaultable_setup_question_correction(
    agent: PersistentAgent,
    *,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> None:
    step_kwargs = {
        "agent": agent,
        "description": (
            "Tool policy: this is a reversible recurring setup request with enough information to proceed. "
            "Do not ask a scope or preference survey; choose reasonable defaults, update __agent_config "
            "charter/schedule with sqlite_batch, and stop unless a real blocker remains."
        ),
    }
    attach_completion(step_kwargs)
    step = PersistentAgentStep.objects.create(**step_kwargs)
    attach_prompt_archive(step)
    logger.info(
        "Agent %s: rejected defaultable setup question and requested sqlite_batch configuration.",
        agent.id,
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
    return isinstance(status, str) and status.lower() in {"warning", "debounced", "throttled"}


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
            "sandbox session is not ready",
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
) -> bool:
    if has_explicit_sleep:
        return False
    if has_canonical_continuation or has_other_tool_calls:
        return True
    return False


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
    "i'll be right here",
    "i will be right here",
    "whenever you're ready",
    "whenever you need",
    "whenever you need me",
    "when you need me",
    "right here whenever",
)
PARALLEL_SAFE_PLACEHOLDER_RE = re.compile(r"\$\[([^\]]+)\]")
PARALLEL_SAFE_OUTPUT_EXTENSIONS = {
    "create_csv": ".csv",
    "create_pdf": ".pdf",
}


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
    if not message_text or "?" in message_text:
        return False
    lower_text = message_text.lower()
    if "$[/" in message_text or "<img" in lower_text or "![](" in message_text:
        return False
    if len(message_text) > 500 and any(marker in lower_text for marker in ("http://", "https://", "**", "###", "source ")):
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
    if django_settings.CELERY_TASK_ALWAYS_EAGER and delay_seconds > 0:
        logger.info(
            "Skipping delayed pending drain scheduling in eager mode (delay=%s).",
            delay_seconds,
        )
        return
    if not claim_pending_drain_slot(ttl=schedule_ttl_seconds):
        return
    try:
        from ..tasks.process_events import process_pending_agent_events_task  # noqa: WPS433 (runtime import)

        process_pending_agent_events_task.apply_async(countdown=delay_seconds)
        if span is not None:
            span.add_event("Pending drain task scheduled")
    except Exception as exc:
        logger.error("Failed to schedule pending drain task: %s", exc)


def _schedule_agent_follow_up(*, agent_id: Union[str, UUID], delay_seconds: int, span=None, reason: str) -> None:
    """Schedule a direct follow-up for a single agent without going through pending-drain."""
    if django_settings.CELERY_TASK_ALWAYS_EAGER and delay_seconds > 0:
        logger.info(
            "Skipping delayed %s follow-up for agent %s in eager mode (delay=%s).",
            reason,
            agent_id,
            delay_seconds,
        )
        return
    try:
        from ..tasks.process_events import process_agent_events_task  # noqa: WPS433 (runtime import)

        process_agent_events_task.apply_async(
            args=[str(agent_id)],
            countdown=delay_seconds,
        )
        if span is not None:
            span.add_event(f"{reason} follow-up scheduled")
    except Exception:
        logger.warning(
            "Failed to schedule %s follow-up for agent %s",
            reason,
            agent_id,
            exc_info=True,
        )


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
    agent_id = lock_key.rsplit(":", 1)[-1]
    return processing_lock_storage_keys(agent_id)


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


def _lock_storage_keys_exist(*, lock_key: str, redis_client) -> bool:
    for storage_key in _lock_storage_keys(lock_key):
        try:
            if redis_client.exists(storage_key):
                return True
        except Exception:
            logger.debug("Failed to check distributed lock key %s", storage_key, exc_info=True)
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
        name = name[:paren_idx].strip()

    if name.startswith("mcp_"):
        repeated_idx = name.find("_mcp_", 4)
        if repeated_idx > 0:
            repeated_name = name[repeated_idx + 1 :].strip()
            if repeated_name.startswith("mcp_"):
                return repeated_name

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


def _tool_context_for_error(
    tool_name: str,
    tool_params: Dict[str, Any] | None,
    *,
    result_content: str | None = None,
    execution_duration_ms: Optional[int] = None,
    status: str | None = None,
    credits_consumed: Any = None,
    consumed_credit: Any = None,
    step: PersistentAgentStep | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_status": status,
        "execution_duration_ms": execution_duration_ms,
        "param_keys": sorted(str(key) for key in tool_params.keys()) if isinstance(tool_params, dict) else [],
        "result_length": len(result_content or ""),
        "credits_consumed": str(credits_consumed) if credits_consumed is not None else None,
        "task_credit_id": str(getattr(consumed_credit, "id", "")) if consumed_credit is not None else None,
    }
    if step is not None:
        context["step_id"] = str(getattr(step, "id", ""))
        context["completion_id"] = str(getattr(step, "completion_id", "")) if getattr(step, "completion_id", None) else None
    return context


def _completion_from_step_kwargs(step_kwargs: dict[str, Any]) -> PersistentAgentCompletion | None:
    completion = step_kwargs.get("completion")
    return completion if isinstance(completion, PersistentAgentCompletion) else None


def _agent_from_step(step: PersistentAgentStep) -> PersistentAgent | None:
    try:
        return step.agent
    except (AttributeError, DatabaseError, PersistentAgent.DoesNotExist):
        return None


def _tool_definition_names_for_completion(tools: list[dict] | None) -> list[str]:
    names: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


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
            log_tool_persistence_error(
                agent,
                retry_exc,
                source="api.agent.core.event_processing._persist_tool_call_step.retry",
                logger=logger,
                completion=_completion_from_step_kwargs(step_kwargs),
                context=_tool_context_for_error(
                    safe_tool_name,
                    tool_params,
                    result_content=normalized_result,
                    execution_duration_ms=execution_duration_ms,
                    status=status or "complete",
                    credits_consumed=credits_consumed,
                    consumed_credit=consumed_credit,
                ),
            )
            return None
    except DatabaseError as db_exc:
        # Data errors, integrity errors, etc. - log and continue
        log_tool_persistence_error(
            agent,
            db_exc,
            source="api.agent.core.event_processing._persist_tool_call_step",
            logger=logger,
            completion=_completion_from_step_kwargs(step_kwargs),
            context=_tool_context_for_error(
                safe_tool_name,
                tool_params,
                result_content=normalized_result,
                execution_duration_ms=execution_duration_ms,
                status=status or "complete",
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
            ),
        )
        return None
    except Exception as exc:
        # Catch-all for unexpected errors - never crash the agent
        log_tool_persistence_error(
            agent,
            exc,
            source="api.agent.core.event_processing._persist_tool_call_step",
            logger=logger,
            completion=_completion_from_step_kwargs(step_kwargs),
            context=_tool_context_for_error(
                safe_tool_name,
                tool_params,
                result_content=normalized_result,
                execution_duration_ms=execution_duration_ms,
                status=status or "complete",
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
            ),
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
    except Exception as exc:
        log_tool_persistence_error(
            agent,
            exc,
            source="api.agent.core.event_processing._create_pending_tool_call_step",
            logger=logger,
            completion=_completion_from_step_kwargs(step_kwargs),
            context=_tool_context_for_error(
                safe_tool_name,
                tool_params,
                status="pending",
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
            ),
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
    except Exception as exc:
        agent = _agent_from_step(step)
        if agent is not None:
            log_tool_persistence_error(
                agent,
                exc,
                source="api.agent.core.event_processing._finalize_pending_tool_call_step.description",
                logger=logger,
                context=_tool_context_for_error(
                    safe_tool_name,
                    tool_params,
                    result_content=normalized_result,
                    execution_duration_ms=execution_duration_ms,
                    status=status,
                    step=step,
                ),
            )
        else:
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
    except Exception as exc:
        agent = _agent_from_step(step)
        if agent is not None:
            log_tool_persistence_error(
                agent,
                exc,
                source="api.agent.core.event_processing._finalize_pending_tool_call_step",
                logger=logger,
                context=_tool_context_for_error(
                    safe_tool_name,
                    tool_params,
                    result_content=normalized_result,
                    execution_duration_ms=execution_duration_ms,
                    status=status,
                    step=step,
                ),
            )
        else:
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


def _clear_refunded_step_charge(step: "PersistentAgentStep") -> None:
    completion_id = getattr(step, "completion_id", None)

    PersistentAgentStep.objects.filter(id=step.id).update(
        credits_cost=None,
        task_credit=None,
    )
    step.credits_cost = None
    step.task_credit = None
    step.task_credit_id = None

    if completion_id is None:
        return

    has_chargeable_sibling = (
        PersistentAgentStep.objects.filter(
            completion_id=completion_id,
            credits_cost__isnull=False,
        )
        .exclude(id=step.id)
        .exists()
    )
    if has_chargeable_sibling:
        return

    PersistentAgentCompletion.objects.filter(id=completion_id).update(credits_cost=None)
    completion = getattr(step, "completion", None)
    if completion is not None:
        completion.credits_cost = None


def _refund_tool_credit_on_error_if_configured(
    *,
    agent: "PersistentAgent",
    tool_name: str,
    step: Optional["PersistentAgentStep"],
    credits_consumed: Any,
    consumed_credit: Any,
) -> None:
    if step is None or not isinstance(credits_consumed, Decimal):
        return
    if consumed_credit is None or not should_refund_tool_credit_on_error(tool_name):
        return

    owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
    if owner is None:
        return

    try:
        with transaction.atomic():
            refund = TaskCreditService.refund_consumed_credit_for_owner(
                owner,
                amount=credits_consumed,
                preferred_credit=consumed_credit,
            )
            if not refund.get("success"):
                logger.warning(
                    "Agent %s: partially refunded errored %s tool call on step %s "
                    "(refunded=%s remaining=%s)",
                    agent.id,
                    tool_name,
                    getattr(step, "id", None),
                    refund.get("refunded"),
                    refund.get("remaining"),
                )
                raise ValueError("Partial tool credit refund")

            _clear_refunded_step_charge(step)
    except (ArithmeticError, DatabaseError, TypeError, ValueError):
        logger.warning(
            "Agent %s: failed to refund credit or clear charge fields for errored %s tool call on step %s",
            agent.id,
            tool_name,
            getattr(step, "id", None),
            exc_info=True,
        )


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


def _parse_tool_call_params(raw_args: Any) -> tuple[Any, Any]:
    """Parse tool-call arguments without altering escape sequences in string values."""
    if isinstance(raw_args, dict):
        return json.dumps(raw_args), raw_args
    raw_args = raw_args or ""
    if raw_args == "":
        return raw_args, {}
    return raw_args, json.loads(raw_args)


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


@dataclass
class _PreparedToolExecution:
    idx: int
    tool_name: str
    tool_params: Dict[str, Any]
    exec_params: Dict[str, Any]
    pending_step: Optional["PersistentAgentStep"]
    credits_consumed: Any
    consumed_credit: Any
    call_id: Optional[str]
    explicit_continue: Optional[bool]
    inferred_continue: bool
    parallel_safe: bool
    parallel_ineligible_reason: Optional[str]


@dataclass
class _ToolExecutionOutcome:
    prepared: _PreparedToolExecution
    result: Any
    duration_ms: int
    updated_tools: Optional[List[dict]]
    variable_map: Dict[str, str]


@dataclass
class _PreparedToolBatch:
    prepared_calls: list[_PreparedToolExecution]
    followup_required: bool
    all_calls_sleep: bool
    abort_after_execution: bool
    parallel_ineligible_reason: Optional[str]


@dataclass
class _ExecutedToolBatch:
    execution_outcomes: list[_ToolExecutionOutcome]
    tools: List[dict]
    abort_after_execution: bool = False


@dataclass
class _FinalizedToolBatch:
    executed_calls: int
    followup_required: bool
    message_delivery_ok: bool
    last_explicit_continue: Optional[bool]
    inferred_message_continue_this_iteration: bool
    executed_non_message_action: bool
    progress_message_delivery_ok: bool = False
    terminal_message_delivery_ok: bool = False
    human_input_request_ok: bool = False


def _plan_has_unfinished_items(agent: PersistentAgent) -> bool:
    try:
        snapshot = build_plan_snapshot(agent)
    except Exception:
        logger.debug("Failed to build plan snapshot for terminal-send check.", exc_info=True)
        return False
    return snapshot.todo_count > 0 or snapshot.doing_count > 0


def _record_terminal_send_unfinished_plan_correction(
    agent: PersistentAgent,
    *,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> None:
    step_kwargs = {
        "agent": agent,
        "description": (
            "Terminal message delivery requested stop, but the current plan still has unfinished items. "
            "Call update_plan with the complete current plan state before stopping."
        ),
    }
    attach_completion(step_kwargs)
    step = PersistentAgentStep.objects.create(**step_kwargs)
    attach_prompt_archive(step)


def _should_skip_stale_planning_mode_after_terminal_delivery(
    agent: PersistentAgent,
    finalized_batch: _FinalizedToolBatch,
    *,
    followup_required: bool,
) -> bool:
    return (
        agent.planning_state == PersistentAgent.PlanningState.PLANNING
        and not followup_required
        and finalized_batch.terminal_message_delivery_ok
        and not finalized_batch.human_input_request_ok
    )


def _skip_stale_planning_mode_after_terminal_delivery(agent: PersistentAgent) -> bool:
    """Clear Planning Mode after a terminal answer slipped through without end_planning.

    This preserves the existing charter instead of guessing a full plan from the answer body.
    """
    from api.services.agent_planning import skip_agent_planning
    from console.agent_chat.signals import emit_agent_planning_state_update

    try:
        updated_agent, cancelled_count = skip_agent_planning(agent)
    except (DatabaseError, PersistentAgent.DoesNotExist):
        logger.exception(
            "Agent %s: failed to clear stale Planning Mode after terminal message delivery.",
            getattr(agent, "id", None),
        )
        return False

    if updated_agent.planning_state == PersistentAgent.PlanningState.PLANNING:
        logger.warning(
            "Agent %s: terminal message delivered but Planning Mode remains active.",
            updated_agent.id,
        )
        return False

    if updated_agent.planning_state == PersistentAgent.PlanningState.SKIPPED:
        emit_agent_planning_state_update(
            updated_agent,
            include_pending_actions=cancelled_count > 0,
        )
        logger.warning(
            "Agent %s: cleared stale Planning Mode after terminal message delivery.",
            updated_agent.id,
        )
    return True


def _latest_inbound_message_needs_reply(agent: PersistentAgent) -> bool:
    latest_inbound = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=False)
        .order_by("-timestamp", "-seq")
        .only("timestamp", "seq")
        .first()
    )
    if latest_inbound is None or latest_inbound.timestamp is None:
        return False

    return not PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
        timestamp__gt=latest_inbound.timestamp,
    ).exists()


def _should_continue_for_unanswered_inbound_after_tools(
    agent: PersistentAgent,
    finalized_batch: _FinalizedToolBatch,
) -> bool:
    return (
        not finalized_batch.followup_required
        and finalized_batch.last_explicit_continue is False
        and finalized_batch.executed_non_message_action
        and not finalized_batch.message_delivery_ok
        and _latest_inbound_message_needs_reply(agent)
    )


def _should_continue_for_pending_progress_reply(
    pending_reply_after_progress: bool,
    finalized_batch: _FinalizedToolBatch,
) -> bool:
    return (
        pending_reply_after_progress
        and not finalized_batch.followup_required
        and finalized_batch.last_explicit_continue is False
        and finalized_batch.executed_non_message_action
        and not finalized_batch.message_delivery_ok
        and not finalized_batch.human_input_request_ok
    )


def _normalize_parallel_placeholder_path(raw: str) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None
    if value.startswith("$[") and value.endswith("]"):
        value = value[2:-1].strip()
    if not value:
        return None
    if value.startswith("/"):
        return value
    if "/" in value:
        return f"/{value}"
    return None


def _collect_parallel_placeholder_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, str):
        for match in PARALLEL_SAFE_PLACEHOLDER_RE.findall(value):
            normalized = _normalize_parallel_placeholder_path(match)
            if normalized:
                paths.add(normalized)
        return paths
    if isinstance(value, dict):
        for item in value.values():
            paths.update(_collect_parallel_placeholder_paths(item))
        return paths
    if isinstance(value, list):
        for item in value:
            paths.update(_collect_parallel_placeholder_paths(item))
    return paths


def _normalized_parallel_read_dependency_path(tool_name: str, tool_params: Dict[str, Any]) -> Optional[str]:
    if tool_name != "read_file":
        return None
    for key in ("path", "file_path", "filename"):
        value = tool_params.get(key)
        if not isinstance(value, str):
            continue
        normalized = _normalize_parallel_placeholder_path(value)
        if normalized:
            return normalized
    return None


def _collect_parallel_dependency_paths(tool_name: str, tool_params: Dict[str, Any]) -> set[str]:
    paths = _collect_parallel_placeholder_paths(tool_params)
    direct_path = _normalized_parallel_read_dependency_path(tool_name, tool_params)
    if direct_path:
        paths.add(direct_path)
    return paths


def _normalized_parallel_output_path(tool_name: str, tool_params: Dict[str, Any]) -> Optional[str]:
    extension = PARALLEL_SAFE_OUTPUT_EXTENSIONS.get(tool_name)
    if not extension:
        return None
    file_path, _overwrite, error = resolve_export_target(tool_params)
    if error or not file_path:
        return None
    normalized = _normalize_write_path(file_path, extension)
    if not normalized:
        return None
    return normalized[3]


def _parallel_batch_ineligible_reason(
    prepared_calls: list[_PreparedToolExecution],
) -> Optional[str]:
    if len(prepared_calls) <= 1:
        return "batch_too_small"

    produced_paths: set[str] = set()
    for prepared in prepared_calls:
        if not prepared.parallel_safe:
            return prepared.parallel_ineligible_reason or f"unsafe_tool:{prepared.tool_name}"
        referenced_paths = _collect_parallel_dependency_paths(
            prepared.tool_name,
            prepared.tool_params,
        )
        if produced_paths.intersection(referenced_paths):
            return f"same_batch_dependency:{prepared.tool_name}"
        output_path = _normalized_parallel_output_path(prepared.tool_name, prepared.tool_params)
        if output_path:
            if output_path in produced_paths:
                return f"duplicate_output:{output_path}"
            produced_paths.add(output_path)

    return None


def _eval_mock_rule_matches(rule: Dict[str, Any], exec_params: Dict[str, Any]) -> bool:
    url_contains = rule.get("url_contains")
    if url_contains is not None:
        url = str(exec_params.get("url") or "").lower()
        expected_parts = [url_contains] if isinstance(url_contains, str) else list(url_contains)
        if not all(str(part).lower() in url for part in expected_parts):
            return False

    url_decoded_contains = rule.get("url_decoded_contains")
    if url_decoded_contains is not None:
        url = unquote_plus(str(exec_params.get("url") or "")).lower()
        expected_parts = (
            [url_decoded_contains]
            if isinstance(url_decoded_contains, str)
            else list(url_decoded_contains)
        )
        if not all(str(part).lower() in url for part in expected_parts):
            return False

    param_contains = rule.get("param_contains")
    if param_contains:
        for key, expected_parts in param_contains.items():
            value = str(exec_params.get(key) or "").lower()
            parts = [expected_parts] if isinstance(expected_parts, str) else list(expected_parts)
            if not all(str(part).lower() in value for part in parts):
                return False

    param_equals = rule.get("param_equals")
    if param_equals:
        for key, expected in param_equals.items():
            if exec_params.get(key) != expected:
                return False

    return True


def _resolve_eval_mock_result(
    mock_config: Optional[Dict[str, Any]],
    tool_name: str,
    exec_params: Dict[str, Any],
) -> Any:
    if not mock_config:
        return None

    mock_result = mock_config.get(tool_name)
    if not isinstance(mock_result, dict) or (
        "rules" not in mock_result and "default" not in mock_result
    ):
        return mock_result

    for rule in mock_result.get("rules") or []:
        if _eval_mock_rule_matches(rule, exec_params):
            return rule.get("result")

    return mock_result.get("default")


_HTTP_URL_PREFIX_RE = re.compile(r"^(https?://[^\s<>'\"\uff5c]+)")


def _strip_linkified_url_artifact(value: str) -> str:
    text = value.strip()
    for separator in ('">', "'>"):
        if separator in text:
            candidate = text.rsplit(separator, 1)[-1].strip()
            if candidate.startswith(("http://", "https://")):
                return candidate
    match = _HTTP_URL_PREFIX_RE.match(text)
    if match and match.group(1) != text:
        return match.group(1)
    return text


def _contact_permission_params_from_misrouted_human_input(
    agent: PersistentAgent,
    tool_params: Dict[str, Any],
) -> Dict[str, Any] | None:
    text = str(tool_params.get("question") or "").strip()
    lowered = text.lower()
    if (
        agent.planning_state == PersistentAgent.PlanningState.PLANNING
        or tool_params.get("requests")
        or tool_params.get("options")
        or not any(term in lowered for term in _CONTACT_APPROVAL_TERMS)
        or not any(term in lowered for term in _CONTACT_SEND_TERMS)
    ):
        return None

    email_match = _EMAIL_ADDRESS_RE.search(text)
    phone_match = next((phone for match in _E164_PHONE_CANDIDATE_RE.finditer(text) if 8 <= len(phone := re.sub(r"\D", "", match.group(0))) <= 15), None)
    if email_match:
        channel, address = CommsChannel.EMAIL, email_match.group(0).lower()
    elif phone_match:
        channel, address = CommsChannel.SMS, f"+{phone_match}"
    else:
        return None

    if agent.is_recipient_whitelisted(channel, address):
        return None

    contact = {
        "channel": channel,
        "address": address,
        "purpose": "Send requested message",
        "reason": "The user asked the agent to contact this recipient; approval is needed before the outbound message can be sent.",
    }
    if channel == CommsChannel.SMS:
        contact["sms_contact_purpose"] = SmsContactPurpose.OTHER_OPERATIONAL
        contact["sms_contact_purpose_details"] = "Operational message requested by the user."
    return {"contacts": [contact]}


def _normalize_tool_params(tool_name: str, tool_params: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "mcp_brightdata_scrape_as_markdown" and isinstance(tool_params.get("url"), str):
        normalized_params = dict(tool_params)
        normalized_params.pop("prompt", None)
        return normalized_params

    if tool_name != "http_request" or not isinstance(tool_params.get("url"), str):
        return tool_params

    normalized_url = _strip_linkified_url_artifact(tool_params["url"])
    if normalized_url == tool_params["url"]:
        return tool_params

    normalized_params = dict(tool_params)
    normalized_params["url"] = normalized_url
    return normalized_params


def _sqlite_batch_statements(tool_params: Dict[str, Any]) -> list[str]:
    raw_sql = tool_params.get("queries", tool_params.get("sql", tool_params.get("query")))
    raw_items = raw_sql if isinstance(raw_sql, list) else [raw_sql]
    statements: list[str] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, str):
            continue
        statements.extend(statement.strip() for statement in sqlparse.split(raw_item) if statement.strip())
    return statements


def _sqlite_batch_is_only_agent_config_mutation(tool_params: Dict[str, Any]) -> bool:
    statements = _sqlite_batch_statements(tool_params)
    return bool(statements) and any(
        AGENT_CONFIG_TABLE_RE.search(statement) and SQLITE_MUTATION_RE.search(statement)
        for statement in statements
    ) and all(AGENT_CONFIG_TABLE_RE.search(statement) for statement in statements)


def _user_text_has_durable_config_intent(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if TRANSIENT_CONFIG_SCOPE_RE.search(normalized) and not STRONG_DURABLE_CONFIG_INTENT_RE.search(normalized):
        return False
    return bool(DURABLE_CONFIG_INTENT_RE.search(normalized))


def _looks_like_one_off_user_task(text: str) -> bool:
    normalized = " ".join((text or "").split())
    return bool(normalized and ONE_OFF_TASK_RE.search(normalized) and not _user_text_has_durable_config_intent(normalized))


def _should_skip_planning_execute_tool_search(agent: PersistentAgent, tool_name: str, prepared_calls: list[_PreparedToolExecution]) -> bool:
    if tool_name != "search_tools" or agent.planning_state != PersistentAgent.PlanningState.PLANNING:
        return False
    if any(call.tool_name not in {"send_chat_message", "sleep_until_next_trigger"} for call in prepared_calls):
        return False
    latest_user_text = " ".join(_latest_inbound_message_text(agent).split())
    return bool(latest_user_text and PLANNING_EXECUTE_NOW_RE.search(latest_user_text))


def _message_tool_body_from_params(tool_name: str, tool_params: Dict[str, Any]) -> str:
    body_key = MESSAGE_TOOL_BODY_KEYS.get(tool_name)
    return str(tool_params.get(body_key) or "") if body_key else ""


def _message_tool_has_progress_intent(tool_name: str, tool_params: Dict[str, Any]) -> bool:
    if tool_name not in MESSAGE_TOOL_NAMES:
        return False
    explicit_continue = _coerce_optional_bool(tool_params.get("will_continue_work"))
    if explicit_continue is True:
        return True
    if explicit_continue is False:
        return False
    return _should_infer_message_tool_continuation(
        _message_tool_body_from_params(tool_name, tool_params)
    )


def _message_tool_is_terminal(tool_name: str, tool_params: Dict[str, Any]) -> bool:
    if tool_name not in MESSAGE_TOOL_NAMES:
        return False
    body = _message_tool_body_from_params(tool_name, tool_params)
    if not body or _looks_like_blocking_human_input_request(body):
        return False
    return not _message_tool_has_progress_intent(tool_name, tool_params)


def _tool_call_likely_terminal_message(call: Any) -> bool:
    tool_name = _get_tool_call_name(call)
    if tool_name not in MESSAGE_TOOL_NAMES:
        return False
    try:
        _raw_args, tool_params = _parse_tool_call_params(_get_tool_call_arguments(call))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return _message_tool_is_terminal(tool_name, tool_params)


def _should_skip_irrelevant_agent_config_mutation(
    agent: PersistentAgent,
    tool_name: str,
    tool_params: Dict[str, Any],
    *,
    batch_has_terminal_message: bool,
) -> bool:
    if tool_name != "sqlite_batch" or not batch_has_terminal_message:
        return False
    if not _sqlite_batch_is_only_agent_config_mutation(tool_params):
        return False
    latest_user_text = _latest_inbound_message_text(agent)
    return _looks_like_one_off_user_task(latest_user_text)


def _record_irrelevant_agent_config_mutation_skip(
    agent: PersistentAgent,
    *,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> None:
    step_kwargs = {
        "agent": agent,
        "description": (
            "Skipped unrelated __agent_config mutation attached to a one-off final answer. "
            "Deliver the requested answer without changing durable charter or schedule."
        ),
    }
    attach_completion(step_kwargs)
    step = PersistentAgentStep.objects.create(**step_kwargs)
    attach_prompt_archive(step)


def _normalize_tool_name_for_execution(agent: PersistentAgent, tool_name: str) -> str:
    entry = resolve_tool_entry(agent, tool_name) if isinstance(tool_name, str) and tool_name.startswith("mcp_") else None
    return entry.full_name if entry else tool_name


def _http_request_dedupe_signature(tool_params: Dict[str, Any]) -> Optional[str]:
    if not isinstance(tool_params, dict) or not isinstance(tool_params.get("url"), str):
        return None

    normalized = {
        key: value
        for key, value in tool_params.items()
        if key != "will_continue_work" and value is not None
    }
    normalized["url"] = _strip_linkified_url_artifact(str(normalized["url"]))
    normalized["method"] = str(normalized.get("method") or "GET").upper()
    try:
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None


def _tool_call_result_text_is_success(result_text: str) -> bool:
    if not result_text:
        return False
    try:
        parsed = json.loads(result_text)
    except json.JSONDecodeError:
        return True
    if not _tool_result_is_success(parsed):
        return False
    if isinstance(parsed, dict):
        status_code = parsed.get("status_code")
        if isinstance(status_code, int) and status_code >= 400:
            return False
    return True


def _current_task_boundary(agent: PersistentAgent) -> Optional[Any]:
    latest_inbound = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, is_outbound=False)
        .order_by("-timestamp", "-seq")
        .values_list("timestamp", flat=True)
        .first()
    )
    latest_cron = (
        PersistentAgentStep.objects.filter(agent=agent, cron_trigger__isnull=False)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    candidates = [candidate for candidate in (latest_inbound, latest_cron) if candidate is not None]
    return max(candidates) if candidates else None


def _find_successful_duplicate_http_request(
    agent: PersistentAgent,
    tool_params: Dict[str, Any],
    *,
    eval_run_id: Optional[str],
) -> Optional[PersistentAgentToolCall]:
    signature = _http_request_dedupe_signature(tool_params)
    if signature is None:
        return None

    queryset = PersistentAgentToolCall.objects.filter(
        step__agent=agent,
        tool_name="http_request",
    ).exclude(status="pending")
    if eval_run_id:
        queryset = queryset.filter(step__eval_run_id=eval_run_id)
    boundary = _current_task_boundary(agent)
    if boundary is not None:
        queryset = queryset.filter(step__created_at__gte=boundary)
    elif not eval_run_id:
        return None

    for prior_call in queryset.order_by("-step__created_at")[:12]:
        prior_params = _normalize_tool_params("http_request", prior_call.tool_params or {})
        if (
            _http_request_dedupe_signature(prior_params) == signature
            and _tool_call_result_text_is_success(prior_call.result)
        ):
            return prior_call
    return None


def _record_duplicate_http_request_skip(
    agent: PersistentAgent,
    prior_call: PersistentAgentToolCall,
    *,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> None:
    step_kwargs = {
        "agent": agent,
        "description": (
            "Skipped duplicate http_request: this exact request already succeeded in this task. "
            f"Use the prior tool result from step {prior_call.step_id}. "
            "If it answers the request, send the final message next; do not refetch or inspect __tool_results just to reread it."
        ),
    }
    attach_completion(step_kwargs)
    step = PersistentAgentStep.objects.create(**step_kwargs)
    attach_prompt_archive(step)


_DIRECT_TOOL_EXECUTORS = {
    "spawn_web_task": "execute_spawn_web_task",
    "send_email": "execute_send_email",
    "send_sms": "execute_send_sms",
    "send_chat_message": "execute_send_chat_message",
    "send_agent_message": "execute_send_agent_message",
    "send_webhook_event": "execute_send_webhook_event",
    "update_schedule": "execute_update_schedule",
    "update_charter": "execute_update_charter",
    "update_plan": "execute_update_plan",
    "secure_credentials_request": "execute_secure_credentials_request",
    "request_contact_permission": "execute_request_contact_permission",
    "request_human_input": "execute_request_human_input",
    "spawn_agent": "execute_spawn_agent",
    "file_str_replace": "execute_file_str_replace",
}

_REFRESHING_TOOL_EXECUTORS = {
    "search_tools": "execute_search_tools",
    CREATE_CUSTOM_TOOL_NAME: "execute_create_custom_tool",
    "end_planning": "execute_end_planning",
}


def _execute_tool_call_runtime(
    agent: PersistentAgent,
    *,
    tool_name: str,
    exec_params: Dict[str, Any],
    budget_ctx: Optional[BudgetContext],
    eval_run_id: Optional[str],
    parallel_safe: bool = False,
) -> tuple[Any, Optional[List[dict]]]:
    updated_tools: Optional[List[dict]] = None
    mock_config = getattr(budget_ctx, "mock_config", None) if budget_ctx else None
    mock_result = _resolve_eval_mock_result(mock_config, tool_name, exec_params)
    if planning_mode_disallows_tool(agent, tool_name):
        return {
            "status": "error",
            "message": f"{tool_name} is unavailable while planning mode is active. Complete or skip planning first.",
        }, updated_tools
    if is_tool_blacklisted_for_agent(agent, tool_name):
        return tool_blacklist_error(tool_name), updated_tools
    if mock_result is not None:
        logger.info(
            "Agent %s: using mock for %s (eval_run_id=%s)",
            agent.id,
            tool_name,
            eval_run_id,
        )
        return mock_result, updated_tools
    if parallel_safe:
        return execute_enabled_tool(
            agent,
            tool_name,
            exec_params,
            isolated_mcp=True,
            current_sqlite_db_path=get_sqlite_db_path(),
        ), updated_tools
    executor_name = _DIRECT_TOOL_EXECUTORS.get(tool_name)
    if executor_name:
        return globals()[executor_name](agent, exec_params), updated_tools
    refreshing_executor_name = _REFRESHING_TOOL_EXECUTORS.get(tool_name)
    if refreshing_executor_name:
        result = globals()[refreshing_executor_name](agent, exec_params)
        updated_tools = get_agent_tools(agent)
        return result, updated_tools
    return execute_enabled_tool(
        agent,
        tool_name,
        exec_params,
        current_sqlite_db_path=get_sqlite_db_path(),
    ), updated_tools


def _tool_result_is_success(result: Any) -> bool:
    if isinstance(result, dict):
        status = str(result.get("status", "")).strip().lower()
        if status in {"error", "failed", "failure"}:
            return False
        if result.get("error") and status not in {"ok", "success"}:
            return False
    return True


def _mark_tool_outcome_failed(
    outcome: _ToolExecutionOutcome,
    exc: Exception,
) -> _ToolExecutionOutcome:
    outcome.result = _build_safe_error_payload(
        f"Tool execution failed: {exc}",
        error_type=type(exc).__name__,
        retryable=_infer_retryable_from_text(str(exc)),
    )
    outcome.updated_tools = None
    return outcome


def _refresh_skills_for_tool_outcome(
    agent: PersistentAgent,
    outcome: _ToolExecutionOutcome,
) -> _ToolExecutionOutcome:
    if not _tool_result_is_success(outcome.result):
        return outcome
    try:
        refresh_skills_for_tool(agent, outcome.prepared.tool_name)
    except DatabaseError as exc:
        logger.exception(
            "Agent %s: skill refresh after tool %s failed (call_id=%s)",
            agent.id,
            outcome.prepared.tool_name,
            outcome.prepared.call_id or "<none>",
        )
        return _mark_tool_outcome_failed(outcome, exc)
    return outcome


def _execute_prepared_tool_call(
    agent: PersistentAgent,
    prepared: _PreparedToolExecution,
    *,
    budget_ctx: Optional[BudgetContext],
    eval_run_id: Optional[str],
    parallel_safe: bool = False,
) -> _ToolExecutionOutcome:
    close_old_connections()
    tool_started_at = time.monotonic()
    try:
        context_step_id = str(prepared.pending_step.id) if prepared.pending_step is not None else None
        with tool_execution_context(step_id=context_step_id):
            result, updated_tools = _execute_tool_call_runtime(
                agent,
                tool_name=prepared.tool_name,
                exec_params=prepared.exec_params,
                budget_ctx=budget_ctx,
                eval_run_id=eval_run_id,
                parallel_safe=parallel_safe,
            )
            if _tool_result_is_success(result) and not parallel_safe:
                refresh_skills_for_tool(agent, prepared.tool_name)
    except Exception as exc:
        logger.exception(
            "Agent %s: tool %s failed (call_id=%s)",
            agent.id,
            prepared.tool_name,
            prepared.call_id or "<none>",
        )
        result = _build_safe_error_payload(
            f"Tool execution failed: {exc}",
            error_type=type(exc).__name__,
            retryable=_infer_retryable_from_text(str(exc)),
        )
        updated_tools = None
    duration_ms = int(round((time.monotonic() - tool_started_at) * 1000))
    return _ToolExecutionOutcome(
        prepared=prepared,
        result=result,
        duration_ms=duration_ms,
        updated_tools=updated_tools,
        variable_map=get_all_variables(),
    )


def _prepare_tool_batch(
    agent: PersistentAgent,
    *,
    tool_calls: list[Any],
    budget_ctx: Optional[BudgetContext],
    eval_run_id: Optional[str],
    heartbeat: Any,
    lock_extender: Any,
    credit_snapshot: Any,
    allow_inferred_message_continue: bool,
    has_non_sleep_calls: bool,
    has_user_facing_message: bool,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> _PreparedToolBatch:
    prepared_calls: list[_PreparedToolExecution] = []
    followup_required = False
    all_calls_sleep = not has_non_sleep_calls
    abort_after_execution = False
    batch_has_human_input_request = any(
        _get_tool_call_name(call) == "request_human_input"
        for call in tool_calls
    )
    batch_has_planning_gate = any(_get_tool_call_name(call) in {"end_planning", "request_human_input"} for call in tool_calls)
    batch_has_terminal_message = any(_tool_call_likely_terminal_message(call) for call in tool_calls)
    skipped_plan_requested_sleep = False

    for idx, call in enumerate(tool_calls, start=1):
        with tracer.start_as_current_span("Prepare Tool") as tool_span:
            if _should_abort_processing(
                agent,
                budget_ctx=budget_ctx,
                heartbeat=heartbeat,
                span=tool_span,
                check_context="tool_batch",
            ):
                abort_after_execution = True
                break
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
                    attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    attach_prompt_archive(step)
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
            logger.info("Agent %s preparing tool %d/%d: %s", agent.id, idx, len(tool_calls), tool_name)

            daily_state = None
            if isinstance(credit_snapshot, dict):
                daily_state = credit_snapshot.get("daily_state")
            if daily_state is None:
                try:
                    daily_state = get_agent_daily_credit_state(agent)
                except Exception:
                    logger.warning(
                        "Failed to load daily credit state while preparing tool batch for agent %s.",
                        agent.id,
                        exc_info=True,
                    )
                    daily_state = None
                if isinstance(credit_snapshot, dict):
                    credit_snapshot["daily_state"] = daily_state

            if (
                is_daily_hard_limit_message_only_mode(daily_state)
                and not is_daily_limit_message_tool(tool_name)
            ):
                try:
                    step_kwargs = {
                        "agent": agent,
                        "description": (
                            "Daily hard limit mode is active. Only message tools are allowed right now: "
                            "send_email, send_sms, send_chat_message, send_agent_message. "
                            f"Do not call {tool_name}; message the user and ask them to raise the limit."
                        ),
                    }
                    attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    attach_prompt_archive(step)
                    logger.info(
                        "Agent %s: rejected %s in daily-limit message-only mode.",
                        agent.id,
                        tool_name,
                    )
                except Exception:
                    logger.debug(
                        "Failed to persist daily-limit message-only correction step for agent %s",
                        agent.id,
                        exc_info=True,
                    )
                followup_required = True
                continue

            if tool_name == "sleep_until_next_trigger":
                if has_non_sleep_calls:
                    logger.info(
                        "Agent %s: ignoring sleep_until_next_trigger because other tools are present in this batch.",
                        agent.id,
                    )
                    continue
                credit_info = _ensure_credit_for_tool(
                    agent,
                    tool_name,
                    span=tool_span,
                    credit_snapshot=credit_snapshot,
                    eval_run_id=eval_run_id,
                )
                if not credit_info:
                    abort_after_execution = True
                    break
                credits_consumed = credit_info.get("cost")
                consumed_credit = credit_info.get("credit")
                step_kwargs = {
                    "agent": agent,
                    "description": "Decided to sleep until next trigger.",
                    "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
                    "task_credit": consumed_credit,
                }
                attach_completion(step_kwargs)
                step = PersistentAgentStep.objects.create(**step_kwargs)
                attach_prompt_archive(step)
                logger.info("Agent %s: sleep_until_next_trigger recorded (will sleep after batch)", agent.id)
                continue

            all_calls_sleep = False
            try:
                raw_args = _get_tool_call_arguments(call)
                raw_args, tool_params = _parse_tool_call_params(raw_args)
            except (TypeError, ValueError, json.JSONDecodeError):
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
                        "Re-send the SAME tool call immediately with valid JSON only; do not switch tools. "
                        "For create_custom_tool, retry create_custom_tool with source_code instead of using create_file. "
                        "For HTML content, use single quotes for all attributes to avoid JSON conflicts."
                    )
                    step_kwargs = {"agent": agent, "description": step_text}
                    attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    attach_prompt_archive(step)
                    logger.info(
                        "Agent %s: added correction step_id=%s to request a retried tool call",
                        agent.id,
                        getattr(step, "id", None),
                    )
                except Exception:
                    logger.debug("Failed to persist correction step", exc_info=True)
                followup_required = True
                break

            if tool_name == "send_chat_message" and not batch_has_human_input_request:
                message_body = str(tool_params.get("body") or "")
                if _looks_like_blocking_human_input_request(message_body):
                    tool_name = "request_human_input"
                    tool_params = _request_human_input_params_from_blocking_chat_question(
                        agent,
                        message_body,
                        tool_params,
                    )
                    logger.info(
                        "Agent %s: routing blocking chat question to request_human_input.",
                        agent.id,
                    )

            if tool_name == "send_chat_message":
                message_body = str(tool_params.get("body") or "")
                if not batch_has_planning_gate and agent.planning_state == PersistentAgent.PlanningState.PLANNING and _coerce_optional_bool(tool_params.get("will_continue_work")) is True and PLANNING_READY_WITHOUT_GATE_RE.search(message_body):
                    step_kwargs = {"agent": agent, "description": "Planning Mode is active and the plan appears clear. Call end_planning(full_plan=...) before ready/start-work chat."}
                    attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    attach_prompt_archive(step)
                    followup_required = True
                    break
                if _should_reject_defaultable_setup_question(agent, message_body):
                    _record_defaultable_setup_question_correction(
                        agent,
                        attach_completion=attach_completion,
                        attach_prompt_archive=attach_prompt_archive,
                    )
                    followup_required = True
                    break

            if tool_name == "request_human_input":
                if any(
                    _should_reject_defaultable_setup_question(agent, question_text)
                    for question_text in _request_human_input_question_texts(tool_params)
                ):
                    _record_defaultable_setup_question_correction(
                        agent,
                        attach_completion=attach_completion,
                        attach_prompt_archive=attach_prompt_archive,
                    )
                    followup_required = True
                    break
                contact_permission_params = _contact_permission_params_from_misrouted_human_input(
                    agent,
                    tool_params,
                )
                if contact_permission_params is not None:
                    logger.info(
                        "Agent %s: routing contact approval question to request_contact_permission.",
                        agent.id,
                    )
                    tool_name = "request_contact_permission"
                    tool_params = contact_permission_params

            if tool_name == "update_plan":
                skipped_plan_result = build_redundant_research_plan_skip_result(agent, tool_params)
                if skipped_plan_result is not None:
                    step_kwargs = {
                        "agent": agent,
                        "description": skipped_plan_result["message"],
                    }
                    attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    attach_prompt_archive(step)
                    logger.info(
                        "Agent %s: skipped redundant research plan update before execution.",
                        agent.id,
                    )
                    if skipped_plan_result.get(AUTO_SLEEP_FLAG) is True:
                        skipped_plan_requested_sleep = True
                    else:
                        followup_required = True
                    continue

            call_id = getattr(call, "id", None)
            if not call_id and isinstance(call, dict):
                call_id = call.get("id")
            if tool_name == "search_tools":
                tool_params.pop("will_continue_work", None)
                if _should_skip_planning_execute_tool_search(agent, tool_name, prepared_calls):
                    step_kwargs = {
                        "agent": agent,
                        "description": (
                            "Skipped search_tools before planning was completed. The user asked to execute now, "
                            "so first call end_planning(full_plan=...) if the plan is sufficient, or request_human_input if blocked."
                        ),
                    }
                    attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    attach_prompt_archive(step)
                    logger.info(
                        "Agent %s: skipped search_tools before planning gate for execute-now prompt.",
                        agent.id,
                    )
                    if not batch_has_planning_gate:
                        followup_required = True
                        break
                    continue
            if (normalized_tool_name := _normalize_tool_name_for_execution(agent, tool_name)) != tool_name:
                logger.info("Agent %s: normalized tool call %s -> %s", agent.id, tool_name, normalized_tool_name)
                tool_name = normalized_tool_name
            tool_params = _normalize_tool_params(tool_name, tool_params)
            if _should_skip_irrelevant_agent_config_mutation(
                agent,
                tool_name,
                tool_params,
                batch_has_terminal_message=batch_has_terminal_message,
            ):
                _record_irrelevant_agent_config_mutation_skip(
                    agent,
                    attach_completion=attach_completion,
                    attach_prompt_archive=attach_prompt_archive,
                )
                logger.info(
                    "Agent %s: skipped irrelevant __agent_config mutation attached to one-off final answer.",
                    agent.id,
                )
                continue
            explicit_continue = _coerce_optional_bool(tool_params.get("will_continue_work"))
            inferred_continue = False
            if tool_name in MESSAGE_TOOL_NAMES:
                body_key = MESSAGE_TOOL_BODY_KEYS.get(tool_name)
                if body_key and isinstance(tool_params.get(body_key), str):
                    cleaned_body, found_phrase = _strip_canonical_continuation_phrase(
                        tool_params[body_key]
                    )
                    if found_phrase:
                        tool_params[body_key] = cleaned_body
                        tool_params["will_continue_work"] = True
                    elif (
                        explicit_continue is None
                        and allow_inferred_message_continue
                        and _should_infer_message_tool_continuation(cleaned_body)
                    ):
                        tool_params["will_continue_work"] = True
                        inferred_continue = True
                        logger.info(
                            "Agent %s: inferred will_continue_work=true for %s based on progress-update language.",
                            agent.id,
                            tool_name,
                        )
                    elif (
                        explicit_continue is None
                        and not allow_inferred_message_continue
                        and _should_infer_message_tool_continuation(cleaned_body)
                    ):
                        logger.info(
                            "Agent %s: suppressing inferred continuation for %s to avoid progress-message loops without work tools.",
                            agent.id,
                            tool_name,
                        )
                explicit_continue = _coerce_optional_bool(tool_params.get("will_continue_work"))

            tool_span.set_attribute("tool.params", json.dumps(tool_params))
            logger.info(
                "Agent %s: %s params=%s",
                agent.id,
                tool_name,
                json.dumps(tool_params)[:ARG_LOG_MAX_CHARS],
            )

            if should_skip_auto_substitution(tool_name):
                exec_params = tool_params
            else:
                exec_params = _substitute_variables_in_params(tool_params)
            if tool_name == "sqlite_batch":
                exec_params = dict(exec_params)
                exec_params["_has_user_facing_message"] = has_user_facing_message

            if tool_name == "http_request":
                duplicate_call = _find_successful_duplicate_http_request(
                    agent,
                    tool_params,
                    eval_run_id=eval_run_id,
                )
                if duplicate_call is not None:
                    _record_duplicate_http_request_skip(
                        agent,
                        duplicate_call,
                        attach_completion=attach_completion,
                        attach_prompt_archive=attach_prompt_archive,
                    )
                    logger.info(
                        "Agent %s: skipped duplicate http_request; prior_step_id=%s",
                        agent.id,
                        duplicate_call.step_id,
                    )
                    followup_required = True
                    continue

            parallel_ineligible_reason = get_parallel_safe_tool_rejection_reason(tool_name, tool_params)

            if not _enforce_tool_rate_limit(
                agent,
                tool_name,
                span=tool_span,
                attach_completion=attach_completion,
                attach_prompt_archive=attach_prompt_archive,
            ):
                followup_required = True
                continue

            credit_info = _ensure_credit_for_tool(
                agent,
                tool_name,
                span=tool_span,
                credit_snapshot=credit_snapshot,
                eval_run_id=eval_run_id,
            )
            if not credit_info:
                abort_after_execution = True
                break
            credits_consumed = credit_info.get("cost")
            consumed_credit = credit_info.get("credit")

            close_old_connections()
            pending_step = _create_pending_tool_call_step(
                agent=agent,
                tool_name=tool_name,
                tool_params=tool_params,
                credits_consumed=credits_consumed,
                consumed_credit=consumed_credit,
                attach_completion=attach_completion,
                attach_prompt_archive=attach_prompt_archive,
            )

            prepared_calls.append(
                _PreparedToolExecution(
                    idx=idx,
                    tool_name=tool_name,
                    tool_params=tool_params,
                    exec_params=exec_params,
                    pending_step=pending_step,
                    credits_consumed=credits_consumed,
                    consumed_credit=consumed_credit,
                    call_id=call_id,
                    explicit_continue=explicit_continue,
                    inferred_continue=inferred_continue,
                    parallel_safe=parallel_ineligible_reason is None,
                    parallel_ineligible_reason=parallel_ineligible_reason,
                )
            )

    return _PreparedToolBatch(
        prepared_calls=prepared_calls,
        followup_required=followup_required,
        all_calls_sleep=all_calls_sleep or (
            skipped_plan_requested_sleep and not prepared_calls and not followup_required
        ),
        abort_after_execution=abort_after_execution,
        parallel_ineligible_reason=_parallel_batch_ineligible_reason(prepared_calls),
    )


def _execute_prepared_tool_batch(
    agent: PersistentAgent,
    prepared_batch: _PreparedToolBatch,
    *,
    budget_ctx: Optional[BudgetContext],
    eval_run_id: Optional[str],
    tools: List[dict],
    heartbeat: Any,
    lock_extender: Any,
) -> _ExecutedToolBatch:
    execution_outcomes: list[_ToolExecutionOutcome] = []
    run_parallel_batch = prepared_batch.parallel_ineligible_reason is None
    available_tools = tools
    abort_after_execution = False

    if run_parallel_batch:
        logger.info(
            "Agent %s: executing %d safe tool calls in parallel.",
            agent.id,
            len(prepared_batch.prepared_calls),
        )
        base_variables = get_all_variables()
        max_workers = min(len(prepared_batch.prepared_calls), max(1, get_max_parallel_tool_calls()))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for prepared in prepared_batch.prepared_calls:
                context = copy_context()
                futures.append(
                    executor.submit(
                        context.run,
                        _execute_prepared_tool_call,
                        agent,
                        prepared,
                        budget_ctx=budget_ctx,
                        eval_run_id=eval_run_id,
                        parallel_safe=True,
                    )
                )
            execution_outcomes = [future.result() for future in futures]

        execution_outcomes = [
            _refresh_skills_for_tool_outcome(agent, outcome)
            for outcome in execution_outcomes
        ]
        merged_variables = dict(base_variables)
        for outcome in sorted(execution_outcomes, key=lambda item: item.prepared.idx):
            merged_variables.update(outcome.variable_map)
        replace_all_variables(merged_variables)
    else:
        if prepared_batch.prepared_calls and prepared_batch.parallel_ineligible_reason:
            logger.info(
                "Agent %s: falling back to serial tool execution (%s).",
                agent.id,
                prepared_batch.parallel_ineligible_reason,
            )
        for prepared in prepared_batch.prepared_calls:
            with tracer.start_as_current_span("Execute Tool") as tool_span:
                if _should_abort_processing(
                    agent,
                    budget_ctx=budget_ctx,
                    heartbeat=heartbeat,
                    span=tool_span,
                    check_context="tool_batch_execute",
                ):
                    abort_after_execution = True
                    break
                if lock_extender:
                    lock_extender.maybe_extend()
                tool_span.set_attribute("persistent_agent.id", str(agent.id))
                tool_span.set_attribute("tool.name", prepared.tool_name)
                outcome = _execute_prepared_tool_call(
                    agent,
                    prepared,
                    budget_ctx=budget_ctx,
                    eval_run_id=eval_run_id,
                    parallel_safe=False,
                )
                execution_outcomes.append(outcome)
                if prepared.tool_name in MESSAGE_TOOL_NAMES:
                    try:
                        agent.refresh_from_db(fields=["signup_preview_state"])
                    except Exception:
                        logger.debug(
                            "Failed to refresh signup preview state after %s for agent %s",
                            prepared.tool_name,
                            agent.id,
                            exc_info=True,
                        )
                    else:
                        if is_signup_preview_processing_paused(agent):
                            logger.info(
                                "Agent %s: stopping serial tool batch after first preview reply.",
                                agent.id,
                            )
                            abort_after_execution = True
                            break
                if outcome.updated_tools is not None:
                    before_count = len(available_tools)
                    available_tools = outcome.updated_tools
                    after_count = len(available_tools)
                    logger.info(
                        "Agent %s: refreshed tools after %s (before=%d after=%d)",
                        agent.id,
                        prepared.tool_name,
                        before_count,
                        after_count,
                    )

    return _ExecutedToolBatch(
        execution_outcomes=execution_outcomes,
        tools=available_tools,
        abort_after_execution=abort_after_execution,
    )


def _finalize_tool_batch(
    agent: PersistentAgent,
    execution_outcomes: list[_ToolExecutionOutcome],
    *,
    attach_completion: Any,
    attach_prompt_archive: Any,
) -> _FinalizedToolBatch:
    executed_calls = 0
    followup_required = False
    message_delivery_ok = False
    last_explicit_continue: Optional[bool] = None
    inferred_message_continue_this_iteration = False
    executed_non_message_action = False
    progress_message_delivery_ok = False
    terminal_message_delivery_ok = False
    human_input_request_ok = False

    for outcome in sorted(execution_outcomes, key=lambda item: item.prepared.idx):
        prepared = outcome.prepared
        result = outcome.result
        tool_name = prepared.tool_name
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
                    prepared.call_id or "<none>",
                )
                result = _build_safe_error_payload(
                    "Tool result serialization failed.",
                    error_type=type(exc).__name__,
                    retryable=False,
                )
                result_content = json.dumps(result)

        try:
            status = result.get("status") if isinstance(result, dict) else None
        except Exception:
            status = None
        result_preview = result_content[:RESULT_LOG_MAX_CHARS]
        logger.info(
            "Agent %s: %s completed status=%s result=%s%s",
            agent.id,
            tool_name,
            status or "",
            result_preview,
            "…" if len(result_content) > len(result_preview) else "",
        )
        message_delivery_skipped = (
            tool_name in MESSAGE_TOOL_NAMES
            and isinstance(result, dict)
            and result.get("skipped") is True
        )
        effective_explicit_continue = prepared.explicit_continue
        if tool_name in MESSAGE_TOOL_NAMES and not message_delivery_skipped:
            status_label = str(status or "").lower()
            if status_label in MESSAGE_SUCCESS_STATUSES:
                message_delivery_ok = True
                if _message_tool_has_progress_intent(tool_name, prepared.tool_params):
                    progress_message_delivery_ok = True
                else:
                    terminal_message_delivery_ok = True

        is_error_status = _is_error_status(result)
        tool_status = "error" if is_error_status else "complete"

        close_old_connections()
        if prepared.pending_step is not None:
            _finalize_pending_tool_call_step(
                step=prepared.pending_step,
                tool_name=tool_name,
                tool_params=prepared.tool_params,
                result_content=result_content,
                execution_duration_ms=outcome.duration_ms,
                status=tool_status,
            )
            step = prepared.pending_step
        else:
            step = _persist_tool_call_step(
                agent=agent,
                tool_name=tool_name,
                tool_params=prepared.tool_params,
                result_content=result_content,
                execution_duration_ms=outcome.duration_ms,
                status=tool_status,
                credits_consumed=prepared.credits_consumed,
                consumed_credit=prepared.consumed_credit,
                attach_completion=attach_completion,
                attach_prompt_archive=attach_prompt_archive,
            )
        if is_error_status:
            _refund_tool_credit_on_error_if_configured(
                agent=agent,
                tool_name=tool_name,
                step=step,
                credits_consumed=prepared.credits_consumed,
                consumed_credit=prepared.consumed_credit,
            )
        elif tool_name == "request_human_input":
            human_input_request_ok = True
        if tool_name == "request_human_input" and isinstance(result, dict):
            attach_originating_step_from_result(step, result)

        allow_auto_sleep = isinstance(result, dict) and result.get(AUTO_SLEEP_FLAG) is True
        tool_had_warning = _is_warning_status(result)
        if effective_explicit_continue is not None:
            last_explicit_continue = effective_explicit_continue
        if effective_explicit_continue is True and prepared.inferred_continue:
            inferred_message_continue_this_iteration = True

        if message_delivery_skipped:
            # Skipped progress-only sends are intentionally not user-visible.
            # Keep the loop alive so the agent can produce the actual reply.
            followup_required = True
        elif tool_name == "search_tools":
            followup_required = True
        elif is_error_status or tool_had_warning:
            followup_required = True
        elif (
            effective_explicit_continue is not True
            and not allow_auto_sleep
            and not terminal_message_delivery_ok
            and not human_input_request_ok
        ):
            followup_required = True

        executed_calls += 1
        if tool_name not in MESSAGE_TOOL_NAMES and tool_name != "sleep_until_next_trigger":
            executed_non_message_action = True

    if (
        agent.planning_state != PersistentAgent.PlanningState.PLANNING
        and terminal_message_delivery_ok
        and not followup_required
        and _plan_has_unfinished_items(agent)
    ):
        _record_terminal_send_unfinished_plan_correction(
            agent,
            attach_completion=attach_completion,
            attach_prompt_archive=attach_prompt_archive,
        )
        followup_required = True

    return _FinalizedToolBatch(
        executed_calls=executed_calls,
        followup_required=followup_required,
        message_delivery_ok=message_delivery_ok,
        last_explicit_continue=last_explicit_continue,
        inferred_message_continue_this_iteration=inferred_message_continue_this_iteration,
        executed_non_message_action=executed_non_message_action,
        progress_message_delivery_ok=progress_message_delivery_ok,
        terminal_message_delivery_ok=terminal_message_delivery_ok,
        human_input_request_ok=human_input_request_ok,
    )


def _gate_send_chat_tool_for_delivery(
    tools: List[dict],
    agent: PersistentAgent,
    *,
    has_deliverable_web_target_now: Optional[bool] = None,
) -> List[dict]:
    """Hide send_chat_message only when no deliverable web target exists and non-web fallback channels are available."""
    if has_deliverable_web_target_now is None:
        has_deliverable_web_target_now = has_deliverable_web_session(agent)
    if has_deliverable_web_target_now:
        return tools
    owner_user = getattr(agent, "user", None)
    if owner_user and not has_other_contact_channel(agent, owner_user):
        return tools

    filtered = [
        tool for tool in tools
        if not (
            isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool.get("function", {}).get("name") == "send_chat_message"
        )
    ]
    return filtered if len(filtered) < len(tools) else tools


def _track_post_completion_deliverable_web_session_activation(
    agent: PersistentAgent,
    *,
    run_sequence_number: Optional[int],
    iteration_index: int,
    retry_switch_active: bool,
    retry_performed: bool,
) -> None:
    """Emit analytics when a deliverable web session appears after completion returns."""
    if not agent.user_id:
        return

    analytics_props: dict[str, Any] = {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "run_sequence_number": run_sequence_number,
        "iteration": iteration_index,
        "retry_reason": "web_session_activated_mid_completion",
        "retry_strategy": "discard_and_rerun_once" if retry_performed else "none",
        "retry_switch_active": retry_switch_active,
        "retry_performed": retry_performed,
        "had_deliverable_web_target_at_start": False,
    }
    props_with_org = Analytics.with_org_properties(
        analytics_props,
        organization=getattr(agent, "organization", None),
    )
    Analytics.track_event(
        user_id=agent.user_id,
        event=AnalyticsEvent.PERSISTENT_AGENT_WEB_SESSION_ACTIVATED_POST_COMPLETION,
        source=AnalyticsSource.AGENT,
        properties=props_with_org,
    )


def _should_retry_after_post_completion_deliverable_web_session_activation(
    agent: PersistentAgent,
    *,
    run_sequence_number: Optional[int],
    iteration_index: int,
    max_remaining: int,
    retry_used: bool,
) -> bool:
    """
    Decide whether to retry once after a deliverable web session appears and emit analytics.

    Returns True when the caller should discard current completion output and retry
    on the next loop iteration.
    """
    retry_switch_active = False
    try:
        retry_switch_active = switch_is_active(
            AGENT_RETRY_COMPLETION_ON_WEB_SESSION_ACTIVATION
        )
    except Exception:
        logger.warning(
            "Failed to evaluate switch %s; skipping mid-completion retry.",
            AGENT_RETRY_COMPLETION_ON_WEB_SESSION_ACTIVATION,
            exc_info=True,
        )
        retry_switch_active = False

    has_iterations_remaining = iteration_index < max_remaining
    retry_performed = (
        retry_switch_active
        and not retry_used
        and has_iterations_remaining
    )

    try:
        _track_post_completion_deliverable_web_session_activation(
            agent,
            run_sequence_number=run_sequence_number,
            iteration_index=iteration_index,
            retry_switch_active=retry_switch_active,
            retry_performed=retry_performed,
        )
    except Exception:
        logger.exception(
            "Failed to emit analytics for post-completion deliverable web-session activation (agent=%s)",
            agent.id,
        )

    if retry_performed:
        logger.info(
            "Agent %s: web session activated mid-completion; discarding completion output and retrying next iteration.",
            agent.id,
        )
        return True

    if retry_switch_active and not has_iterations_remaining:
        logger.info(
            "Agent %s: web session activated mid-completion but no iterations remain; processing current completion.",
            agent.id,
        )
    return False


def _get_latest_deliverable_web_session(agent: PersistentAgent):
    for session in get_deliverable_web_sessions(agent):
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

    Implied delivery is limited to active web chat sessions.
    """
    from .prompt_context import _get_implied_send_context

    ctx = _get_implied_send_context(agent)
    if not ctx:
        return None, "Implied send failed: no active recipient context."

    channel = ctx.get("channel")
    to_address = ctx.get("to_address")
    eval_web_fallback = bool(ctx.get("eval_web_fallback")) and agent.execution_environment == "eval"
    if not eval_web_fallback and not has_deliverable_web_session(agent):
        return None, "Implied send failed: no deliverable web session."
    if channel != "web":
        return None, "Implied send failed: active web session required."

    if _looks_like_blocking_human_input_request(message_text):
        tool_params = {
            "question": _extract_human_input_question(message_text),
            "will_continue_work": will_continue_work,
        }
        return (
            {
                "id": "implied_human_input",
                "function": {"name": "request_human_input", "arguments": json.dumps(tool_params)},
            },
            None,
        )

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

def _attempt_cycle_close_for_sleep(agent: PersistentAgent, budget_ctx: Optional[BudgetContext]) -> None:
    """Best-effort attempt to close the budget cycle when the agent goes idle."""

    if budget_ctx is None:
        return

    # If follow-ups are queued, keep the cycle open so they can run.
    try:
        redis_client = get_redis_client()
        if is_agent_pending(agent.id, client=redis_client) or is_processing_queued(agent.id, client=redis_client):
            logger.info(
                "Agent %s sleeping with queued follow-up work; keeping cycle active.",
                agent.id,
            )
            return
    except Exception:
        logger.debug("Follow-up state check failed; proceeding to default close logic", exc_info=True)

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


def _get_processing_abort_reason(agent_id: Union[str, UUID]) -> str | None:
    try:
        close_old_connections()
        lifecycle_state = (
            PersistentAgent.objects.filter(id=agent_id)
            .values("is_deleted", "is_active")
            .first()
        )
    except DatabaseError:
        logger.debug(
            "Lifecycle guard lookup failed for agent %s; continuing processing.",
            agent_id,
            exc_info=True,
        )
        return None

    if lifecycle_state is None:
        return "missing"
    if lifecycle_state["is_deleted"]:
        return "soft_deleted"
    if not lifecycle_state["is_active"]:
        return "inactive"
    return None


def _should_abort_for_inactive_or_deleted_agent(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    heartbeat: Optional[_ProcessingHeartbeat],
    span: Any,
    check_context: str,
) -> bool:
    reason = _get_processing_abort_reason(agent.id)
    if reason is None:
        return False

    clear_processing_work_state(agent.id)
    logger.info(
        "Agent %s became unavailable during processing (%s, reason=%s); aborting loop.",
        agent.id,
        check_context,
        reason,
    )
    try:
        span.add_event(
            "Agent processing aborted by lifecycle state",
            {"context": check_context, "reason": reason},
        )
    except Exception:
        pass
    if heartbeat:
        heartbeat.touch(f"agent_{reason}")
    _attempt_cycle_close_for_sleep(agent, budget_ctx)
    return True


def _should_abort_for_stop_request(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    heartbeat: Optional[_ProcessingHeartbeat],
    span: Any,
    check_context: str,
    redis_client=None,
) -> bool:
    if not is_processing_stop_requested(agent.id, client=redis_client):
        return False

    clear_processing_stop_requested(agent.id, client=redis_client)
    clear_processing_work_state(agent.id, client=redis_client)
    logger.info(
        "Agent %s stop requested during processing (%s); aborting loop gracefully.",
        agent.id,
        check_context,
    )
    try:
        span.add_event(
            "Agent processing aborted by stop request",
            {"context": check_context},
        )
    except Exception:
        pass
    if heartbeat:
        heartbeat.touch("agent_stop_requested")
    _attempt_cycle_close_for_sleep(agent, budget_ctx)
    return True


def _should_abort_processing(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    heartbeat: Optional[_ProcessingHeartbeat],
    span: Any,
    check_context: str,
    redis_client=None,
) -> bool:
    if _should_abort_for_inactive_or_deleted_agent(
        agent,
        budget_ctx=budget_ctx,
        heartbeat=heartbeat,
        span=span,
        check_context=check_context,
    ):
        return True
    return _should_abort_for_stop_request(
        agent,
        budget_ctx=budget_ctx,
        heartbeat=heartbeat,
        span=span,
        check_context=check_context,
        redis_client=redis_client,
    )


def _should_stop_for_eval_policy(
    agent: PersistentAgent,
    *,
    budget_ctx: Optional[BudgetContext],
    span: Any,
) -> bool:
    if not budget_ctx or not budget_ctx.eval_run_id or not budget_ctx.eval_stop_policy:
        return False
    try:
        from api.evals.stop_policy import should_stop_for_eval_policy

        should_stop, reason = should_stop_for_eval_policy(
            budget_ctx.eval_run_id,
            budget_ctx.eval_stop_policy,
        )
    except Exception:
        logger.exception("Failed to evaluate eval stop policy for agent %s", agent.id)
        return False
    if not should_stop:
        return False

    logger.info(
        "Agent %s: stopping eval processing early for run %s (%s).",
        agent.id,
        budget_ctx.eval_run_id,
        reason or "eval stop policy matched",
    )
    try:
        span.add_event(
            "Eval stop policy matched",
            {
                "eval_run_id": str(budget_ctx.eval_run_id),
                "reason": reason or "",
            },
        )
    except Exception:
        pass
    return True


def _close_active_cycle_for_skipped_agent(
    agent_id: Union[str, UUID],
    *,
    budget_id: str | None,
    span: Any,
    check_context: str,
) -> None:
    if not budget_id:
        return

    try:
        status = AgentBudgetManager.get_cycle_status(agent_id=str(agent_id))
        active_id = AgentBudgetManager.get_active_budget_id(agent_id=str(agent_id))
        if status == "active" and active_id == str(budget_id):
            AgentBudgetManager.close_cycle(agent_id=str(agent_id), budget_id=str(budget_id))
            logger.info(
                "Closed active budget cycle for skipped agent %s (%s, budget_id=%s).",
                agent_id,
                check_context,
                budget_id,
            )
            try:
                span.add_event(
                    "Closed active budget cycle for skipped agent",
                    {"context": check_context, "budget_id": str(budget_id)},
                )
            except Exception:
                pass
    except Exception:
        logger.debug(
            "Failed to close active budget cycle for skipped agent %s (%s).",
            agent_id,
            check_context,
            exc_info=True,
        )


def _should_skip_processing_for_inactive_or_deleted_agent(
    agent_id: Union[str, UUID],
    *,
    budget_id: str | None,
    span: Any,
    check_context: str,
) -> bool:
    reason = _get_processing_abort_reason(agent_id)
    if reason is None:
        return False

    clear_processing_work_state(agent_id)
    _close_active_cycle_for_skipped_agent(
        agent_id,
        budget_id=budget_id,
        span=span,
        check_context=check_context,
    )
    logger.info(
        "Skipping event processing for agent %s (%s, reason=%s).",
        agent_id,
        check_context,
        reason,
    )
    try:
        span.add_event(
            "Agent processing skipped by lifecycle state",
            {"context": check_context, "reason": reason},
        )
    except Exception:
        pass
    return True


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
    stream_content: bool = True,
    stale_prompt_checker: Callable[[], bool] | None = None,
) -> Any:
    if stream_broadcaster:
        stream_broadcaster.start()

    content_filter = _CanonicalContinuationStreamFilter() if stream_broadcaster else None
    accumulator = StreamAccumulator()
    start_time = time.monotonic()
    canceled = False
    stream = None

    def _close_stream() -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.debug("Failed to close stale orchestrator stream", exc_info=True)

    try:
        if stale_prompt_checker and stale_prompt_checker():
            canceled = True
            if stream_broadcaster:
                stream_broadcaster.cancel()
            raise OrchestratorPromptStale("Prompt became stale before streaming completion started.")
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
            if stale_prompt_checker and stale_prompt_checker():
                canceled = True
                _close_stream()
                if stream_broadcaster:
                    stream_broadcaster.cancel()
                raise OrchestratorPromptStale("Prompt became stale during streaming completion.")
            reasoning_delta, content_delta = accumulator.ingest_chunk(chunk)
            if stream_broadcaster:
                filtered_delta = None
                if stream_content:
                    filtered_delta = content_filter.ingest(content_delta) if content_filter else content_delta
                stream_broadcaster.push_delta(reasoning_delta, filtered_delta)
    finally:
        if stream_broadcaster and not canceled:
            trailing = content_filter.flush() if content_filter and stream_content else None
            if trailing:
                stream_broadcaster.push_delta(None, trailing)
            stream_broadcaster.finish()

    response = accumulator.build_response(model=model, provider=provider)
    response.request_duration_ms = int(round((time.monotonic() - start_time) * 1000))
    raise_if_empty_litellm_response(response, model=model, provider=provider)
    raise_if_invalid_litellm_response(response, model=model, provider=provider)
    return response


def _attach_completion_runtime_hints(response: Any, **hints: Any) -> None:
    if response is None or not hints:
        return

    if isinstance(response, dict):
        model_extra = response.setdefault("model_extra", {})
    else:
        model_extra = getattr(response, "model_extra", None)
        if not isinstance(model_extra, dict):
            model_extra = {}
            try:
                setattr(response, "model_extra", model_extra)
            except Exception:
                return

    runtime_hints = model_extra.setdefault("gobii_runtime_hints", {})
    if isinstance(runtime_hints, dict):
        runtime_hints.update(hints)


def _get_completion_runtime_hints(response: Any) -> dict[str, Any]:
    if response is None:
        return {}

    if isinstance(response, dict):
        model_extra = response.get("model_extra")
    else:
        model_extra = getattr(response, "model_extra", None)

    if not isinstance(model_extra, dict):
        return {}

    runtime_hints = model_extra.get("gobii_runtime_hints")
    if not isinstance(runtime_hints, dict):
        return {}

    return runtime_hints


def _llm_provider_candidates_for_error_context(failover_configs: List[Tuple[str, str, dict]] | None) -> list[dict]:
    candidates: list[dict] = []
    for config in failover_configs or []:
        try:
            provider, model, _params = config
        except (TypeError, ValueError):
            candidates.append({"raw": str(config)})
            continue
        candidates.append({"provider": provider, "model": model})
    return candidates


def _preferred_config_for_error_context(preferred_config: Optional[Tuple[str, str]]) -> dict | None:
    if not preferred_config:
        return None
    if isinstance(preferred_config, (list, tuple)) and len(preferred_config) >= 2:
        return {
            "provider": preferred_config[0],
            "model": preferred_config[1],
        }
    return {"raw": str(preferred_config)}


def _completion_with_failover(
    messages: List[dict],
    tools: List[dict],
    failover_configs: List[Tuple[str, str, dict]],
    agent_id: str = None,
    safety_identifier: str = None,
    preferred_config: Optional[Tuple[str, str]] = None,
    stream_broadcaster: Optional[WebStreamBroadcaster] = None,
    allow_streamed_content: bool = True,
    stale_prompt_checker: Callable[[], bool] | None = None,
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
        allow_streamed_content: Whether assistant message text is allowed to stream to the UI
        
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
        if stale_prompt_checker and stale_prompt_checker():
            raise OrchestratorPromptStale("Prompt became stale before completion request was sent.")
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
                logger.info(
                    "LLM call: provider=%s model=%s api_base=%s api_key=%s",
                    provider,
                    model,
                    api_base or "",
                    "<redacted>" if api_key_present else "<none>",
                )

                # If OpenAI family, add safety_identifier hint when available
                request_messages = base_messages
                request_tools_payload: Optional[List[dict]] = list(base_tools) if base_tools else None

                if (provider.startswith("openai") or provider == "openai") and safety_identifier:
                    params["safety_identifier"] = str(safety_identifier)

                if active_stream_broadcaster:
                    stream_content = allow_streamed_content and bool(
                        params_base.get("allow_implied_send", True)
                    )
                    try:
                        response = _stream_completion_with_broadcast(
                            model=model,
                            messages=request_messages,
                            params=params,
                            tools=request_tools_payload,
                            provider=provider,
                            stream_broadcaster=active_stream_broadcaster,
                            stream_content=stream_content,
                            stale_prompt_checker=stale_prompt_checker,
                        )
                    except OrchestratorPromptStale:
                        raise
                    except Exception:
                        if stale_prompt_checker and stale_prompt_checker():
                            raise OrchestratorPromptStale(
                                "Prompt became stale during streaming completion."
                            )
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
                if stale_prompt_checker and stale_prompt_checker():
                    raise OrchestratorPromptStale("Prompt became stale before completion response was accepted.")

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
                _attach_completion_runtime_hints(
                    response,
                    allow_implied_send=bool(params_base.get("allow_implied_send", True)),
                )
                set_usage_span_attributes(llm_span, usage)

                return response, token_usage

        except OrchestratorPromptStale:
            if active_stream_broadcaster:
                active_stream_broadcaster.cancel()
            raise
        except Exception as exc:
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


def _prepare_multimodal_read_file_completion_request(
    *,
    agent: PersistentAgent,
    history: List[dict],
    failover_configs: List[Tuple[str, str, dict]],
    image_attachments: list,
    fitted_token_count: int,
    is_first_run: bool,
    routing_profile: Any,
    prefer_low_latency: Optional[bool],
) -> tuple[List[dict], List[Tuple[str, str, dict]], bool]:
    candidate_failover_configs = failover_configs
    if not any(bool((params or {}).get("supports_vision")) for _, _, params in failover_configs):
        try:
            candidate_failover_configs = get_llm_config_with_failover(
                agent_id=str(agent.id),
                token_count=fitted_token_count,
                agent=agent,
                is_first_loop=is_first_run,
                routing_profile=routing_profile,
                prefer_low_latency=prefer_low_latency,
                ignore_agent_tier_cap=True,
            )
        except LLMNotConfiguredError:
            candidate_failover_configs = failover_configs

    request_history, request_failover_configs, multimodal_attached = prepare_multimodal_read_file_request(
        history,
        candidate_failover_configs,
        image_attachments,
    )
    if not multimodal_attached:
        request_failover_configs = failover_configs
    return request_history, request_failover_configs, multimodal_attached


def _get_completed_process_run_count(agent: Optional[PersistentAgent]) -> int:
    """Return how many PROCESS_EVENTS loops completed for the agent."""
    if agent is None:
        return 0

    return PersistentAgentSystemStep.objects.filter(
        step__agent=agent,
        code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
        step__description="Process events",
    ).count()


def _create_agent_system_step_once(
    *,
    agent: PersistentAgent,
    description: str,
    code: str,
    notes: str,
) -> bool:
    if PersistentAgentSystemStep.objects.filter(
        step__agent=agent,
        code=code,
        notes=notes,
    ).exists():
        return False

    step = PersistentAgentStep.objects.create(
        agent=agent,
        description=description,
    )
    PersistentAgentSystemStep.objects.create(
        step=step,
        code=code,
        notes=notes,
    )
    return True


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
    eval_run_id: Optional[str] = None,
) -> dict[str, Any] | Literal[False]:
    """Ensure the agent's owner has a task credit and consume it just-in-time.

    Returns False if insufficient or consumption fails. On success, returns a dict
    containing the consumed cost and the TaskCredit (if any), so callers can attach
    them to persisted steps for accurate usage attribution.
    """
    if tool_name == "send_chat_message":
        return {"cost": None, "credit": None}

    if is_eval_credit_exempt_context(agent=agent, eval_run_id=eval_run_id):
        if span is not None:
            try:
                span.add_event("Eval credit bypass active")
                span.set_attribute("credit_check.eval_bypass", True)
            except Exception:
                pass
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

    if can_bypass_task_credit_for_signup_preview(agent):
        if span is not None:
            try:
                span.add_event("Signup preview credit bypass active")
            except Exception:
                pass
        return {"cost": None, "credit": None}

    cost: Decimal | None = None
    consumed: dict | None = None
    consumed_credit = None

    # Determine tool cost up-front so we can gate on fractional balances
    try:
        cost = get_tool_credit_cost(tool_name)
    except Exception as e:
        log_credit_failure(
            agent,
            e,
            source="api.agent.core.event_processing._ensure_credit_for_tool.cost",
            logger=logger,
            context={
                "operation": "get_tool_credit_cost",
                "tool_name": tool_name,
                "owner_label": owner_label,
                "owner_type": "organization" if owner_is_org else "user",
                "owner_id": str(getattr(owner, "id", "")) if owner is not None else None,
                "user_id": str(getattr(owner_user, "id", "")) if owner_user is not None else None,
                "fallback": "default_task_credit_cost",
            },
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
            log_credit_failure(
                agent,
                e,
                source="api.agent.core.event_processing._ensure_credit_for_tool.availability",
                logger=logger,
                context={
                    "operation": "calculate_available_tasks",
                    "tool_name": tool_name,
                    "owner_label": owner_label,
                    "owner_type": "organization" if owner_is_org else "user",
                    "owner_id": str(getattr(owner, "id", "")) if owner is not None else None,
                    "user_id": str(getattr(owner_user, "id", "")) if owner_user is not None else None,
                    "cost": str(cost) if cost is not None else None,
                },
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

    if is_daily_hard_limit_message_only_mode(daily_state) and is_daily_limit_message_tool(tool_name):
        if available is not None and available != TASKS_UNLIMITED and Decimal(available) <= Decimal("0"):
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
                "Agent %s insufficient credits mid-loop while in daily-limit message-only mode.",
                agent.id,
            )
            return False
        if span is not None:
            try:
                span.add_event("Message tool allowed in daily-limit message-only mode")
                span.set_attribute("credit_check.daily_limit_message_only_mode", True)
            except Exception:
                pass
        return {"cost": None, "credit": None}

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
        log_credit_failure(
            agent,
            e,
            source="api.agent.core.event_processing._ensure_credit_for_tool",
            logger=logger,
            context={
                "operation": "consume_credit",
                "tool_name": tool_name,
                "owner_label": owner_label,
                "owner_type": "organization" if owner_is_org else "user",
                "owner_id": str(getattr(owner, "id", "")) if owner is not None else None,
                "user_id": str(getattr(owner_user, "id", "")) if owner_user is not None else None,
                "cost": str(cost) if cost is not None else None,
                "available": str(available) if available is not None else None,
                "daily_hard_limit": str(hard_limit) if hard_limit is not None else None,
                "daily_hard_remaining": str(hard_remaining) if hard_remaining is not None else None,
            },
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
    eval_stop_policy: Optional[Dict[str, Any]] = None,
    burn_follow_up_token: Optional[str] = None,
    inbound_generation: int | str | None = None,
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
    if is_human_inbound_generation_consumed(
        persistent_agent_id,
        inbound_generation,
        client=redis_client,
    ):
        logger.info(
            "Skipping event processing for agent %s – inbound generation %s already consumed.",
            persistent_agent_id,
            inbound_generation,
        )
        span.add_event("Processing skipped - inbound generation already consumed")
        clear_processing_queued_flag(persistent_agent_id, client=redis_client)
        return

    if burn_follow_up_token:
        logger.info(
            "Ignoring obsolete burn-rate follow-up token for agent %s.",
            persistent_agent_id,
        )

    if _should_skip_processing_for_inactive_or_deleted_agent(
        persistent_agent_id,
        budget_id=budget_id,
        span=span,
        check_context="entry",
    ):
        return

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
        eval_stop_policy=eval_stop_policy,
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
        if is_processing_stop_requested(persistent_agent_id, client=redis_client):
            clear_processing_stop_requested(persistent_agent_id, client=redis_client)
            clear_processing_work_state(persistent_agent_id, client=redis_client)
            _close_active_cycle_for_skipped_agent(
                persistent_agent_id,
                budget_id=getattr(ctx, "budget_id", None),
                span=span,
                check_context="lock_acquired_stop_requested",
            )
            logger.info(
                "Skipping event processing for agent %s due to pending stop request after lock acquisition.",
                persistent_agent_id,
            )
            span.add_event("Processing skipped - stop requested after lock acquisition")
            return
        mark_processing_lock_active(persistent_agent_id, client=redis_client)
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
            lock_released = False
            try:
                lock.release()
                lock_released = True
                logger.info("Released distributed lock for agent %s", persistent_agent_id)
                span.add_event("Distributed lock released")
            except Exception as e:
                logger.warning("Failed to release lock for agent %s: %s", persistent_agent_id, str(e))
                span.add_event("Lock release warning")
            if lock_released or not _lock_storage_keys_exist(
                lock_key=lock_key,
                redis_client=redis_client,
            ):
                clear_processing_lock_active(persistent_agent_id, client=redis_client)
        if heartbeat:
            heartbeat.clear()

        # Clear local budget context
        set_budget_context(None)

        # Broadcast final processing state to websocket clients after all processing is complete
        try:
            from console.agent_chat.signals import _broadcast_processing

            agent_obj = processed_agent
            if agent_obj is None:
                agent_obj = PersistentAgent.objects.alive().filter(id=persistent_agent_id).first()
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
    budget_ctx = get_budget_context()
    try:
        agent = (
            PersistentAgent.objects.alive().select_related(
                "organization",
                "organization__billing",
                "user",
                "user__billing",
                "preferred_contact_endpoint",
                "browser_use_agent",
            )
            .prefetch_related("webhooks")
            .get(id=persistent_agent_id)
        )
    except PersistentAgent.DoesNotExist:
        clear_processing_work_state(persistent_agent_id)
        _close_active_cycle_for_skipped_agent(
            persistent_agent_id,
            budget_id=getattr(budget_ctx, "budget_id", None),
            span=span,
            check_context="locked_missing",
        )
        logger.warning("Persistent agent %s not found; skipping processing.", persistent_agent_id)
        return None

    if not agent.is_active:
        clear_processing_work_state(agent.id)
        _close_active_cycle_for_skipped_agent(
            agent.id,
            budget_id=getattr(budget_ctx, "budget_id", None),
            span=span,
            check_context="locked_inactive",
        )
        logger.info("Persistent agent %s is inactive; skipping processing.", persistent_agent_id)
        span.add_event("Agent processing skipped - inactive")
        span.set_attribute("persistent_agent.is_active", False)
        return agent

    if is_signup_preview_processing_paused(agent):
        clear_processing_work_state(agent.id)
        _close_active_cycle_for_skipped_agent(
            agent.id,
            budget_id=getattr(budget_ctx, "budget_id", None),
            span=span,
            check_context="signup_preview_waiting_for_completion",
        )
        logger.info(
            "Persistent agent %s is paused awaiting signup completion; skipping processing.",
            persistent_agent_id,
        )
        span.add_event("Agent processing skipped - signup preview awaiting completion")
        span.set_attribute("persistent_agent.signup_preview_state", agent.signup_preview_state)
        return agent

    # Broadcast processing state at start of processing (when lock is acquired)
    try:
        from console.agent_chat.signals import _broadcast_processing

        _broadcast_processing(agent)
    except Exception as e:
        logger.debug("Failed to broadcast processing state at start for agent %s: %s", persistent_agent_id, e)

    owner = resolve_agent_owner(agent)
    pause_state = get_owner_execution_pause_state(owner)
    if pause_state["paused"]:
        pause_reason = pause_state["reason"] or "unknown"
        msg = f"Skipped processing because {EXECUTION_PAUSE_MESSAGE.lower()}"
        pause_note = f"{EXECUTION_PAUSE_NOTE}:{pause_reason}"
        logger.warning(
            "Persistent agent %s skipped because owner execution is paused (reason=%s).",
            persistent_agent_id,
            pause_reason,
        )

        _create_agent_system_step_once(
            agent=agent,
            description=msg,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            notes=pause_note,
        )

        span.add_event("Agent processing skipped - owner execution paused")
        span.set_attribute("owner.execution_paused", True)
        span.set_attribute("owner.execution_pause_reason", pause_reason)
        return agent

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

            _create_agent_system_step_once(
                agent=agent,
                description=msg,
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

        if is_eval_credit_exempt_context(agent=agent, eval_run_id=getattr(budget_ctx, "eval_run_id", None)):
            credit_snapshot = {"available": None, "daily_state": {}}
            span.add_event("Eval credit gate bypassed")
            span.set_attribute("credit_check.eval_bypass", True)
        elif settings.GOBII_PROPRIETARY_MODE:
            owner_user = getattr(agent, "user", None)
            owner_is_org = TaskCreditService._is_organization_owner(owner) if owner is not None else False
            if owner is not None:
                if can_bypass_task_credit_for_signup_preview(agent):
                    span.add_event("Signup preview credit gate bypassed")
                    span.set_attribute("credit_check.signup_preview_bypass", True)
                    credit_snapshot = {"available": None, "daily_state": {}}
                else:
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

                    daily_limit_exhausted = daily_limit is not None and (
                        daily_remaining is None or daily_remaining <= Decimal("0")
                    )
                    if daily_limit_exhausted:
                        msg = (
                            "Agent reached its enforced daily task credit limit and is entering message-only mode."
                        )
                        logger.warning(
                            "Persistent agent %s reached hard daily limit before loop; continuing in message-only mode (used=%s limit=%s).",
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

                        span.add_event("Agent processing entering daily-limit message-only mode")
                        span.set_attribute("credit_check.daily_limit_block", True)

                    if (
                        not daily_limit_exhausted
                        and available is not None
                        and available != TASKS_UNLIMITED
                        and Decimal(available) <= Decimal("0")
                    ):
                        msg = "Skipped processing due to insufficient credits (proprietary mode)."
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

        _run_agent_loop(
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
    clear_runtime_tier_override(agent)
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
    current_planning_state = agent.planning_state
    owner = resolve_agent_owner(agent)
    # Completion billing metadata is effectively scoped to this processing run,
    # so resolve it once instead of repeating owner plan lookups each iteration.
    billing_snapshot = get_billing_snapshot_for_owner(owner)

    # Track cumulative token usage across all iterations
    cumulative_token_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "model": None,
        "provider": None
    }

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
    pending_reply_after_progress = False
    continuation_notice: Optional[str] = None
    web_session_activation_retry_used = False
    empty_response_loop_retries = 0

    def _current_human_inbound_generation() -> int:
        return get_human_inbound_generation(agent.id, client=redis_client)

    try:
        for i in range(max_remaining):
            previous_planning_state = current_planning_state
            try:
                agent.refresh_from_db(fields=["planning_state", "updated_at"])
            except PersistentAgent.DoesNotExist:
                logger.info("Agent %s no longer exists; stopping loop.", agent.id)
                clear_processing_work_state(agent.id, client=redis_client)
                _close_active_cycle_for_skipped_agent(
                    agent.id,
                    budget_id=getattr(budget_ctx, "budget_id", None),
                    span=span,
                    check_context="loop_refresh_missing",
                )
                return cumulative_token_usage

            current_planning_state = agent.planning_state
            if current_planning_state != previous_planning_state:
                tools = get_agent_tools(agent)

            had_deliverable_web_target_at_start = has_deliverable_web_session(agent)
            if _should_abort_processing(
                agent,
                budget_ctx=budget_ctx,
                heartbeat=heartbeat,
                span=span,
                check_context="iteration_start",
                redis_client=redis_client,
            ):
                return cumulative_token_usage
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
                _schedule_agent_follow_up(
                    agent_id=agent.id,
                    delay_seconds=pending_settings.pending_drain_delay_seconds,
                    span=span,
                    reason="Runtime limit",
                )
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

                iteration_tools = _gate_send_chat_tool_for_delivery(
                    tools,
                    agent,
                    has_deliverable_web_target_now=had_deliverable_web_target_at_start,
                )
                if is_daily_hard_limit_message_only_mode(daily_state):
                    iteration_tools = filter_tools_for_daily_limit_message_only_mode(iteration_tools)
                iter_span.set_attribute("persistent_agent.tools.count", len(iteration_tools))

                burn_rate_action = handle_burn_rate_limit(
                    agent,
                    budget_ctx=budget_ctx,
                    span=iter_span,
                    daily_state=daily_state,
                )
                judge_trigger_reasons = []
                if burn_rate_action == BurnRateAction.STEPPED_DOWN:
                    judge_trigger_reasons.append("burn_rate_tier_step_down")
                maybe_run_agent_judge(agent, tools=tools, extra_trigger_reasons=judge_trigger_reasons)

                prompt_human_generation = _current_human_inbound_generation()
                config_snapshot = seed_sqlite_agent_config(agent)
                skills_snapshot = seed_sqlite_skills(agent)
                current_notice = continuation_notice
                continuation_notice = None
                routing_profile = get_current_eval_routing_profile()
                prefer_low_latency = had_deliverable_web_target_at_start
                try:
                    prompt_context_result = build_prompt_context(
                        agent,
                        current_iteration=i + 1,
                        max_iterations=MAX_AGENT_LOOP_ITERATIONS,
                        reasoning_only_streak=reasoning_only_streak,
                        is_first_run=is_first_run,
                        daily_credit_state=daily_state,
                        continuation_notice=current_notice,
                        routing_profile=routing_profile,
                        prefer_low_latency=prefer_low_latency,
                        include_metadata=True,
                    )
                except Exception as exc:
                    log_prompt_construction_error(
                        agent,
                        exc,
                        source="api.agent.core.event_processing._run_agent_loop",
                        logger=logger,
                        context={
                            "agent_id": str(agent.id),
                            "run_sequence_number": run_sequence_number,
                            "iteration": i + 1,
                            "max_iterations": MAX_AGENT_LOOP_ITERATIONS,
                            "is_first_run": is_first_run,
                            "reasoning_only_streak": reasoning_only_streak,
                            "has_continuation_notice": bool(current_notice),
                            "routing_profile": getattr(routing_profile, "name", None),
                            "prefer_low_latency": prefer_low_latency,
                        },
                    )
                    raise
                if len(prompt_context_result) == 4:
                    history, fitted_token_count, prompt_archive_id, prompt_metadata = prompt_context_result
                else:
                    history, fitted_token_count, prompt_archive_id = prompt_context_result
                    prompt_metadata = {}
                prompt_allows_implied_send = bool(prompt_metadata.get("prompt_allows_implied_send", True))
                prompt_archive_attached = False
                latest_human_generation = _current_human_inbound_generation()
                if latest_human_generation > prompt_human_generation:
                    logger.info(
                        "Agent %s: human input generation changed from %s to %s while building prompt; rebuilding.",
                        agent.id,
                        prompt_human_generation,
                        latest_human_generation,
                    )
                    continuation_notice = (
                        "A newer user message arrived while the last prompt was being prepared; "
                        "rebuild the prompt and answer the latest message."
                    )
                    continue

                accepted_human_generation = latest_human_generation

                # Atomically consume one global step only after accepting the prompt.
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

                def _is_orchestrator_prompt_stale() -> bool:
                    return _current_human_inbound_generation() > accepted_human_generation

                def _mark_accepted_human_generation_consumed() -> None:
                    if accepted_human_generation <= 0:
                        return
                    if _is_orchestrator_prompt_stale():
                        return
                    mark_human_inbound_generation_consumed(
                        agent.id,
                        accepted_human_generation,
                        client=redis_client,
                    )
                    if not _is_orchestrator_prompt_stale():
                        remove_pending_agent(agent.id, client=redis_client)

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
                failover_configs = prompt_metadata.get("prompt_failover_configs")
                if not failover_configs:
                    try:
                        failover_configs = get_llm_config_with_failover(
                            agent_id=str(agent.id),
                            token_count=fitted_token_count,
                            agent=agent,
                            is_first_loop=is_first_run,
                            routing_profile=routing_profile,
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
                request_history = history
                request_failover_configs = failover_configs
                fresh_tool_call_step_ids = prompt_metadata.get("fresh_tool_call_step_ids") or []
                image_attachments = collect_fresh_read_file_image_attachments(
                    agent,
                    fresh_tool_call_step_ids,
                )
                if image_attachments:
                    (
                        request_history,
                        request_failover_configs,
                        multimodal_attached,
                    ) = _prepare_multimodal_read_file_completion_request(
                        agent=agent,
                        history=history,
                        failover_configs=failover_configs,
                        image_attachments=image_attachments,
                        fitted_token_count=fitted_token_count,
                        is_first_run=is_first_run,
                        routing_profile=routing_profile,
                        prefer_low_latency=prefer_low_latency,
                    )
                    if multimodal_attached:
                        logger.info(
                            "Agent %s: attached %d read_file image(s) to multimodal orchestrator request",
                            agent.id,
                            len(image_attachments),
                        )
                    else:
                        logger.info(
                            "Agent %s: read_file image context available but no vision-capable orchestrator endpoint found",
                            agent.id,
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
                        messages=request_history,
                        tools=iteration_tools,
                        failover_configs=request_failover_configs,
                        agent_id=str(agent.id),
                        safety_identifier=agent.user.id if agent.user else None,
                        preferred_config=preferred_config,
                        stream_broadcaster=stream_broadcaster,
                        allow_streamed_content=prompt_allows_implied_send,
                        stale_prompt_checker=_is_orchestrator_prompt_stale,
                    )
                    empty_response_loop_retries = 0
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

                except OrchestratorPromptStale:
                    latest_human_generation = _current_human_inbound_generation()
                    logger.info(
                        "Agent %s: discarded stale orchestrator completion for generation %s; latest is %s.",
                        agent.id,
                        accepted_human_generation,
                        latest_human_generation,
                    )
                    iter_span.add_event(
                        "Orchestrator completion discarded due to newer human input",
                        {
                            "accepted_generation": accepted_human_generation,
                            "latest_generation": latest_human_generation,
                        },
                    )
                    if heartbeat:
                        heartbeat.touch("prompt_stale")
                    continuation_notice = (
                        "A newer user message arrived while the previous response was being generated; "
                        "discard that stale response and answer using the latest conversation state."
                    )
                    continue
                except Exception as e:
                    if (
                        isinstance(e, EmptyLiteLLMResponseError)
                        and empty_response_loop_retries < settings.AGENT_EMPTY_LLM_RESPONSE_LOOP_RETRIES
                    ):
                        empty_response_loop_retries += 1
                        logger.warning(
                            "Agent %s: provider returned empty completions after internal retries; retrying agent loop (%s/%s).",
                            agent.id,
                            empty_response_loop_retries,
                            settings.AGENT_EMPTY_LLM_RESPONSE_LOOP_RETRIES,
                        )
                        if heartbeat:
                            heartbeat.touch("llm_empty_response_loop_retry")
                        continue

                    current_span = trace.get_current_span()
                    mark_span_failed_with_exception(current_span, e, "LLM completion failed with all providers")
                    log_agent_error(
                        agent,
                        category=PersistentAgentError.Category.LLM_COMPLETION,
                        source="api.agent.core.event_processing._run_agent_loop",
                        message=f"LLM call failed for agent {agent.id} with all providers",
                        exc=e,
                        logger=logger,
                        context={
                            "agent_id": str(agent.id),
                            "provider_candidates": _llm_provider_candidates_for_error_context(failover_configs),
                            "preferred_config": _preferred_config_for_error_context(preferred_config),
                            "run_sequence_number": run_sequence_number,
                            "iteration": i + 1,
                        },
                    )
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
                            llm_tool_names=_tool_definition_names_for_completion(iteration_tools),
                            thinking_content=thinking_content,
                            **billing_snapshot,
                            **token_usage_fields,
                        )
                    return completion

                suppress_step_completion_billing = is_daily_hard_limit_message_only_mode(
                    daily_state
                )

                # Persist completion immediately so token usage isn't lost if execution exits early.
                # In daily-limit message-only mode we keep the completion record, but we do not
                # attach it to steps because PersistentAgentStep.save() would otherwise consume
                # more task credits via completion billing.
                _ensure_completion()

                deliverable_web_session_activated_post_completion = (
                    not had_deliverable_web_target_at_start and has_deliverable_web_session(agent)
                )
                if deliverable_web_session_activated_post_completion:
                    if _should_retry_after_post_completion_deliverable_web_session_activation(
                        agent,
                        run_sequence_number=run_sequence_number,
                        iteration_index=i + 1,
                        max_remaining=max_remaining,
                        retry_used=web_session_activation_retry_used,
                    ):
                        web_session_activation_retry_used = True
                        continuation_notice = (
                            "Web chat became active mid-run; rerunning once with updated tool availability."
                        )
                        continue

                def _attach_completion(step_kwargs: dict) -> None:
                    if suppress_step_completion_billing:
                        return
                    completion_obj = _ensure_completion()
                    step_kwargs["completion"] = completion_obj

                def _persist_reasoning_step(reasoning_source: Optional[str]) -> Optional[PersistentAgentStep]:
                    reasoning_text = (reasoning_source or "").strip()
                    if not reasoning_text:
                        return None
                    step_kwargs = {
                        "agent": agent,
                        "description": internal_reasoning.build_internal_reasoning_description(reasoning_text),
                    }
                    _attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    _attach_prompt_archive(step)
                    return step

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

                def _apply_skill_updates() -> tuple[bool, bool]:
                    """Apply skill updates and return (had_errors, changed)."""
                    skill_apply = apply_sqlite_skill_updates(agent, skills_snapshot)

                    if not skill_apply.errors:
                        return False, bool(skill_apply.changed)

                    for error in skill_apply.errors:
                        try:
                            step_kwargs = {
                                "agent": agent,
                                "description": f"Skill update failed: {error}",
                            }
                            _attach_completion(step_kwargs)
                            step = PersistentAgentStep.objects.create(**step_kwargs)
                            _attach_prompt_archive(step)
                        except Exception:
                            logger.debug(
                                "Failed to persist skill update error step for agent %s",
                                agent.id,
                                exc_info=True,
                            )
                    return True, bool(skill_apply.changed)

                def _apply_runtime_updates() -> bool:
                    # Some unit tests call _run_agent_loop directly without agent_sqlite_db().
                    # In that mode, reconciliation has no SQLite state to diff against.
                    if not get_sqlite_db_path():
                        logger.debug(
                            "Agent %s: skipping runtime SQLite reconciliation (no db path).",
                            agent.id,
                        )
                        return False
                    config_errors = _apply_agent_config_updates()
                    skill_errors, skills_changed = _apply_skill_updates()
                    if skills_changed:
                        nonlocal tools
                        tools = get_agent_tools(agent)
                    return config_errors or skill_errors

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
                runtime_hints = _get_completion_runtime_hints(response)
                selected_model_allows_implied_send = bool(
                    runtime_hints.get("allow_implied_send", True)
                )
                implied_send_allowed = prompt_allows_implied_send and selected_model_allows_implied_send
                implied_send_disabled_reason = None
                if not prompt_allows_implied_send:
                    implied_send_disabled_reason = "Implied send disabled by prompt configuration."
                elif not selected_model_allows_implied_send:
                    implied_send_disabled_reason = "Implied send disabled for the selected model."
                if message_text and not has_explicit_send:
                    # Default: STOP. Agent must explicitly request continuation with "CONTINUE_WORK_SIGNAL".
                    # This is safer—agent won't keep running unexpectedly.
                    implied_will_continue = _should_imply_continue(
                        has_canonical_continuation=has_canonical_continuation,
                        has_other_tool_calls=has_other_tool_calls,
                        has_explicit_sleep=has_explicit_sleep,
                    )
                    if implied_send_allowed:
                        implied_call, implied_error = _build_implied_send_tool_call(
                            agent,
                            message_text,
                            will_continue_work=implied_will_continue,
                        )
                    else:
                        implied_call, implied_error = None, implied_send_disabled_reason
                    if implied_call:
                        implied_send = True
                        implied_stop_after_send = not implied_will_continue  # Stop unless continuation phrase
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
                        if not implied_send_allowed:
                            logger.info(
                                "Agent %s: skipping implied-send correction step because implied send is disabled by configuration.",
                                agent.id,
                            )
                            implied_error = None
                            # Treat config-level opt-out as a normal choice rather than a delivery failure.
                        else:
                            try:
                                step_kwargs = {
                                    "agent": agent,
                                    "description": (
                                        "Message delivery requires explicit send tools when implied send is unavailable. "
                                        "If send_chat_message is unavailable, retry with send_email/send_sms using the user's most "
                                        "recently active non-web communication channel from unified history/recent contacts."
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

                reasoning_step = _persist_reasoning_step(reasoning_source)

                if not tool_calls:
                    if _apply_runtime_updates():
                        reasoning_only_streak = 0
                        _mark_accepted_human_generation_consumed()
                        continue
                    if not message_text and not thinking_content:
                        # Truly empty response (no text, no thinking, no tools) = agent is done
                        # Log plan state to help diagnose premature termination
                        plan_state = "unknown"
                        try:
                            from api.agent.tools.plan import build_plan_snapshot
                            snap = build_plan_snapshot(agent)
                            if snap:
                                plan_state = f"todo={snap.todo_count}, doing={snap.doing_count}, done={snap.done_count}"
                        except (DatabaseError, LookupError, RuntimeError):
                            logger.debug("Failed to build plan snapshot for termination log", exc_info=True)
                        logger.info(
                            "Agent %s: empty response (no message, no thinking, no tools), auto-sleeping. "
                            "Plan at termination: %s. Raw msg_content type=%s, len=%s",
                            agent.id,
                            plan_state,
                            type(msg_content).__name__,
                            len(msg_content) if msg_content else 0,
                        )
                        _mark_accepted_human_generation_consumed()
                        _attempt_cycle_close_for_sleep(agent, budget_ctx)
                        return cumulative_token_usage
                    if reasoning_step is not None:
                        try:
                            reasoning_step.description = internal_reasoning.build_internal_reasoning_description(
                                reasoning_source,
                                reasoning_only=True,
                            )
                            reasoning_step.save(update_fields=["description"])
                        except Exception:
                            logger.debug(
                                "Failed to mark reasoning-only step for agent %s",
                                agent.id,
                                exc_info=True,
                            )
                    # Message or thinking content but no tools - increment streak.
                    # Thinking-only models (e.g., DeepSeek) put responses in thinking blocks;
                    # don't auto-sleep just because message_text is empty.
                    reasoning_only_streak += 1

                    # Check for continuation signals like "let me", "I'll", "I'm going to"
                    # in message or thinking content - gives agent one extra pass.
                    has_continuation = _has_continuation_signal(raw_message_text) or _has_continuation_signal(thinking_content or "")
                    effective_limit = MAX_NO_TOOL_STREAK + 1 if has_continuation else MAX_NO_TOOL_STREAK

                    if reasoning_only_streak >= effective_limit:
                        # Log plan state to help diagnose premature termination
                        plan_state = "unknown"
                        try:
                            from api.agent.tools.plan import build_plan_snapshot
                            snap = build_plan_snapshot(agent)
                            if snap:
                                plan_state = f"todo={snap.todo_count}, doing={snap.doing_count}, done={snap.done_count}"
                                if snap.todo_count > 0 or snap.doing_count > 0:
                                    logger.warning(
                                        "Agent %s: auto-sleeping with unfinished plan work! %s",
                                        agent.id,
                                        plan_state,
                                    )
                        except (DatabaseError, LookupError, RuntimeError):
                            logger.debug("Failed to build plan snapshot for termination log", exc_info=True)
                        logger.info(
                            "Agent %s: %d consecutive responses without tool calls (limit=%d), auto-sleeping. "
                            "Plan: %s. Last message preview: %.100s",
                            agent.id,
                            reasoning_only_streak,
                            effective_limit,
                            plan_state,
                            message_text or thinking_content or "(none)",
                        )
                        _mark_accepted_human_generation_consumed()
                        _attempt_cycle_close_for_sleep(agent, budget_ctx)
                        return cumulative_token_usage
                    _mark_accepted_human_generation_consumed()
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
                prepared_batch = _prepare_tool_batch(
                    agent,
                    tool_calls=list(tool_calls or []),
                    budget_ctx=budget_ctx,
                    eval_run_id=eval_run_id,
                    heartbeat=heartbeat,
                    lock_extender=lock_extender,
                    credit_snapshot=credit_snapshot,
                    allow_inferred_message_continue=allow_inferred_message_continue,
                    has_non_sleep_calls=has_non_sleep_calls,
                    has_user_facing_message=has_user_facing_message,
                    attach_completion=_attach_completion,
                    attach_prompt_archive=_attach_prompt_archive,
                )
                followup_required = prepared_batch.followup_required
                all_calls_sleep = prepared_batch.all_calls_sleep

                if _should_stop_for_eval_policy(agent, budget_ctx=budget_ctx, span=iter_span):
                    _mark_accepted_human_generation_consumed()
                    _attempt_cycle_close_for_sleep(agent, budget_ctx)
                    return cumulative_token_usage

                executed_batch = _execute_prepared_tool_batch(
                    agent,
                    prepared_batch,
                    budget_ctx=budget_ctx,
                    eval_run_id=eval_run_id,
                    tools=tools,
                    heartbeat=heartbeat,
                    lock_extender=lock_extender,
                )
                tools = executed_batch.tools

                finalized_batch = _finalize_tool_batch(
                    agent,
                    executed_batch.execution_outcomes,
                    attach_completion=_attach_completion,
                    attach_prompt_archive=_attach_prompt_archive,
                )
                executed_calls = finalized_batch.executed_calls
                followup_required = followup_required or finalized_batch.followup_required
                message_delivery_ok = finalized_batch.message_delivery_ok
                last_explicit_continue = finalized_batch.last_explicit_continue
                inferred_message_continue_this_iteration = (
                    finalized_batch.inferred_message_continue_this_iteration
                )
                executed_non_message_action = finalized_batch.executed_non_message_action

                if prepared_batch.abort_after_execution or executed_batch.abort_after_execution:
                    try:
                        agent.refresh_from_db(fields=["signup_preview_state"])
                    except Exception:
                        logger.debug(
                            "Failed to refresh signup preview state before abort for agent %s",
                            agent.id,
                            exc_info=True,
                        )
                    else:
                        if is_signup_preview_processing_paused(agent):
                            logger.info(
                                "Agent %s: pausing processing after signup preview reply.",
                                agent.id,
                            )
                            _attempt_cycle_close_for_sleep(agent, budget_ctx)
                    _mark_accepted_human_generation_consumed()
                    return cumulative_token_usage

                if _apply_runtime_updates():
                    followup_required = True

                if _should_stop_for_eval_policy(agent, budget_ctx=budget_ctx, span=iter_span):
                    _mark_accepted_human_generation_consumed()
                    _attempt_cycle_close_for_sleep(agent, budget_ctx)
                    return cumulative_token_usage

                _mark_accepted_human_generation_consumed()

                if finalized_batch.terminal_message_delivery_ok or finalized_batch.human_input_request_ok:
                    pending_reply_after_progress = False
                elif finalized_batch.progress_message_delivery_ok:
                    pending_reply_after_progress = True

                if executed_non_message_action:
                    inferred_message_continue_streak = 0
                elif inferred_message_continue_this_iteration:
                    inferred_message_continue_streak += 1
                else:
                    inferred_message_continue_streak = 0

                if _should_skip_stale_planning_mode_after_terminal_delivery(
                    agent,
                    finalized_batch,
                    followup_required=followup_required,
                ):
                    if not _skip_stale_planning_mode_after_terminal_delivery(agent):
                        followup_required = True

                if all_calls_sleep:
                    logger.info("Agent %s is sleeping.", agent.id)
                    _attempt_cycle_close_for_sleep(agent, budget_ctx)
                    return cumulative_token_usage
                elif _should_continue_for_unanswered_inbound_after_tools(agent, finalized_batch):
                    logger.info(
                        "Agent %s: non-message tool batch requested stop while latest inbound message "
                        "is still unanswered; continuing for a user-facing reply.",
                        agent.id,
                    )
                elif _should_continue_for_pending_progress_reply(pending_reply_after_progress, finalized_batch):
                    logger.info(
                        "Agent %s: non-message tool batch requested stop after a progress reply; "
                        "continuing for the user-facing answer.",
                        agent.id,
                    )
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
            _schedule_agent_follow_up(
                agent_id=agent.id,
                delay_seconds=delay_seconds,
                span=span,
                reason="Max iterations",
            )
            _attempt_cycle_close_for_sleep(agent, budget_ctx)

        return cumulative_token_usage
    finally:
        clear_runtime_tier_override(agent)
