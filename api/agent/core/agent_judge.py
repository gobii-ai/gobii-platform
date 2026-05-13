"""Internal LLM judge for persistent-agent trajectory quality."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from waffle import get_waffle_flag_model

from api.agent.core.llm_config import LLMNotConfiguredError, get_agent_judge_llm_config, get_agent_llm_tier
from api.agent.core.llm_utils import run_completion
from api.agent.core.token_usage import log_agent_completion
from api.agent.tools.plan import build_plan_snapshot
from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentJudgeSuggestion,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemMessage,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)
from constants.feature_flags import PERSISTENT_AGENT_LLM_JUDGE

logger = logging.getLogger(__name__)

JUDGE_FAILED_TOOL_THRESHOLD = 3
JUDGE_RECENT_TOOL_LIMIT = 12
JUDGE_RECENT_MESSAGE_LIMIT = 12
JUDGE_RECENT_STEP_LIMIT = 12
JUDGE_RUN_CACHE_TTL_SECONDS = 60 * 60 * 12
JUDGE_MIN_STEP_GAP = 10
JUDGE_RUN_COOLDOWN_SECONDS = 60 * 45
JUDGE_DAILY_RUN_LIMIT = 6
REPORT_TOOL_NAME = "report_judge_suggestion"
NO_ACTION = "no_action"
ALLOWED_SUGGESTION_TYPES = {
    PersistentAgentJudgeSuggestion.SuggestionType.INTELLIGENCE_UPGRADE,
    PersistentAgentJudgeSuggestion.SuggestionType.STONEWALL_REFRAME,
    PersistentAgentJudgeSuggestion.SuggestionType.REQUEST_HUMAN_INPUT,
    PersistentAgentJudgeSuggestion.SuggestionType.STRATEGY_SHIFT,
    NO_ACTION,
}
NEGATIVE_LANGUAGE_PATTERNS = (
    "not working",
    "still broken",
    "you already",
    "i already",
    "again",
    "stuck",
    "why can't",
    "stop repeating",
    "same thing",
    "frustrating",
    "useless",
    "wrong",
)
BLOCKER_PATTERNS = (
    "can't",
    "cannot",
    "unable",
    "need more information",
    "need you to",
    "i need",
    "blocked",
    "not able",
)


@dataclass(frozen=True)
class JudgeTrigger:
    reasons: list[str]
    evidence_hash: str
    trajectory: dict[str, Any]
    non_judge_step_count: int


def maybe_run_agent_judge(agent: PersistentAgent, *, tools: list[dict[str, Any]] | None = None) -> None:
    """Run the internal judge when heuristics indicate the agent may need guidance."""

    try:
        if not is_agent_judge_enabled_for_agent(agent):
            return
        trigger = build_judge_trigger(agent, tools=tools)
        if trigger is None:
            return
        _run_judge(agent, trigger)
    except Exception:
        # The judge is advisory. A failure here must never interrupt agent work.
        logger.exception("Agent judge failed for agent %s", getattr(agent, "id", None))


def run_manual_agent_judge(agent: PersistentAgent, *, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run the judge from staff tooling, bypassing automatic trigger throttles."""

    trigger = build_manual_judge_trigger(agent, tools=tools)
    return _run_judge(agent, trigger, cache_evidence=False)


def build_manual_judge_trigger(agent: PersistentAgent, *, tools: list[dict[str, Any]] | None = None) -> JudgeTrigger:
    non_judge_step_count = _count_non_judge_steps(agent)
    recent_messages = _recent_messages(agent)
    recent_tool_calls = _recent_tool_calls(agent)
    reasons = ["manual_audit"]
    trajectory = _build_trajectory_packet(
        agent,
        tools=tools or [],
        recent_messages=recent_messages,
        recent_tool_calls=recent_tool_calls,
        trigger_reasons=reasons,
        non_judge_step_count=non_judge_step_count,
    )
    evidence_hash = _hash_payload(
        {
            "agent_id": str(agent.id),
            "manual_run_at": timezone.now().isoformat(),
            "step_count": non_judge_step_count,
            "reasons": reasons,
            "message_ids": [item["id"] for item in trajectory["recent_messages"]],
            "tool_step_ids": [item["step_id"] for item in trajectory["recent_tool_calls"]],
        }
    )
    return JudgeTrigger(
        reasons=reasons,
        evidence_hash=evidence_hash,
        trajectory=trajectory,
        non_judge_step_count=non_judge_step_count,
    )


def build_judge_trigger(agent: PersistentAgent, *, tools: list[dict[str, Any]] | None = None) -> JudgeTrigger | None:
    if not is_agent_judge_enabled_for_agent(agent):
        return None

    non_judge_step_count = _count_non_judge_steps(agent)
    if non_judge_step_count <= 0:
        return None

    latest_completion_at = _latest_judge_completion_created_at(agent)
    if (
        _recent_judge_completion_step_count(agent, non_judge_step_count, latest_completion_at) < JUDGE_MIN_STEP_GAP
        or _recent_judge_suggestion_step_count(agent, non_judge_step_count) < JUDGE_MIN_STEP_GAP
    ):
        return None

    if _is_judge_completion_in_cooldown(latest_completion_at):
        return None

    if _judge_completion_count_today(agent) >= JUDGE_DAILY_RUN_LIMIT:
        return None

    recent_messages = _recent_messages(agent)
    recent_tool_calls = _recent_tool_calls(agent)
    reasons = _trigger_reasons(recent_messages, recent_tool_calls)
    if not reasons:
        return None

    trajectory = _build_trajectory_packet(
        agent,
        tools=tools or [],
        recent_messages=recent_messages,
        recent_tool_calls=recent_tool_calls,
        trigger_reasons=reasons,
        non_judge_step_count=non_judge_step_count,
    )
    evidence_hash = _hash_payload(
        {
            "agent_id": str(agent.id),
            "step_count": non_judge_step_count,
            "reasons": reasons,
            "message_ids": [item["id"] for item in trajectory["recent_messages"]],
            "tool_step_ids": [item["step_id"] for item in trajectory["recent_tool_calls"]],
        }
    )
    if cache.get(_judge_run_cache_key(agent, evidence_hash)):
        return None

    return JudgeTrigger(
        reasons=reasons,
        evidence_hash=evidence_hash,
        trajectory=trajectory,
        non_judge_step_count=non_judge_step_count,
    )


def _run_judge(agent: PersistentAgent, trigger: JudgeTrigger, *, cache_evidence: bool = True) -> dict[str, Any]:
    if cache_evidence:
        cache.set(_judge_run_cache_key(agent, trigger.evidence_hash), True, timeout=JUDGE_RUN_CACHE_TTL_SECONDS)

    try:
        config = get_agent_judge_llm_config()
    except LLMNotConfiguredError:
        logger.info("Skipping agent judge for %s because no LLM config is available.", agent.id)
        return {"ran": False, "status": "llm_not_configured"}

    messages = _build_judge_messages(trigger.trajectory)
    tool_def = _judge_tool_definition()
    provider, model, params = config
    judge_params = dict(params or {})
    judge_params["tool_choice"] = {"type": "function", "function": {"name": REPORT_TOOL_NAME}}
    response = run_completion(
        model=model,
        messages=messages,
        params=judge_params,
        tools=[tool_def],
        drop_params=True,
    )
    log_agent_completion(
        agent,
        completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
        response=response,
        model=model,
        provider=provider,
    )
    payload = _extract_report_payload(response)
    if payload is None:
        logger.info("Agent judge for %s returned no report tool call.", agent.id)
        return {"ran": True, "status": "missing_report_tool_call"}
    report_judge_suggestion(agent, trigger, payload)
    return {
        "ran": True,
        "status": "completed",
        "suggestion_type": _clean_choice(payload.get("suggestion_type")) or None,
    }


def report_judge_suggestion(agent: PersistentAgent, trigger: JudgeTrigger, payload: dict[str, Any]) -> None:
    suggestion_type = _clean_choice(payload.get("suggestion_type"))
    if suggestion_type not in ALLOWED_SUGGESTION_TYPES:
        suggestion_type = PersistentAgentJudgeSuggestion.SuggestionType.STRATEGY_SHIFT
    if suggestion_type == NO_ACTION:
        return

    if PersistentAgentJudgeSuggestion.objects.filter(
        agent=agent,
        suggestion_type=suggestion_type,
        evidence_hash=trigger.evidence_hash,
    ).exists():
        return

    title = _clean_text(payload.get("title"), default=_default_title(suggestion_type), max_length=255)
    ui_message = _clean_text(payload.get("ui_message"), default=title, max_length=1200)
    agent_directive = _clean_text(payload.get("agent_directive"), default=ui_message, max_length=2000)
    recommended_tier = _clean_text(payload.get("recommended_tier"), default="", max_length=64)
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    confidence = _coerce_confidence(payload.get("confidence"))

    try:
        with transaction.atomic():
            step = PersistentAgentStep.objects.create(
                agent=agent,
                description=f"LLM judge suggestion ({suggestion_type}): {title}\n{agent_directive}",
            )
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION,
                notes=f"evidence_hash={trigger.evidence_hash};confidence={confidence:.2f}",
            )
            system_message = PersistentAgentSystemMessage.objects.create(
                agent=agent,
                body=_format_agent_directive(title, agent_directive, suggestion_type),
            )
            PersistentAgentJudgeSuggestion.objects.create(
                agent=agent,
                suggestion_type=suggestion_type,
                title=title,
                ui_message=ui_message,
                agent_directive=agent_directive,
                confidence=confidence,
                recommended_tier=recommended_tier,
                evidence=evidence,
                trigger_reasons=trigger.reasons,
                evidence_hash=trigger.evidence_hash,
                source_step=step,
                system_message=system_message,
            )
    except IntegrityError:
        return


def _count_non_judge_steps(agent: PersistentAgent) -> int:
    return (
        PersistentAgentStep.objects.filter(agent=agent)
        .exclude(system_step__code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION)
        .count()
    )


def _recent_judge_suggestion_step_count(agent: PersistentAgent, non_judge_step_count: int) -> int:
    latest = (
        PersistentAgentJudgeSuggestion.objects.filter(agent=agent)
        .order_by("-created_at")
        .values_list("source_step__created_at", flat=True)
        .first()
    )
    if latest is None:
        return JUDGE_MIN_STEP_GAP
    later_count = (
        PersistentAgentStep.objects.filter(agent=agent, created_at__gt=latest)
        .exclude(system_step__code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION)
        .count()
    )
    return max(0, min(non_judge_step_count, later_count))


def _latest_judge_completion_created_at(agent: PersistentAgent):
    return (
        PersistentAgentCompletion.objects
        .filter(agent=agent, completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )


def _recent_judge_completion_step_count(
    agent: PersistentAgent,
    non_judge_step_count: int,
    latest,
) -> int:
    if latest is None:
        return JUDGE_MIN_STEP_GAP
    later_count = (
        PersistentAgentStep.objects.filter(agent=agent, created_at__gt=latest)
        .exclude(system_step__code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION)
        .count()
    )
    return max(0, min(non_judge_step_count, later_count))


def _is_judge_completion_in_cooldown(latest) -> bool:
    if latest is None:
        return False
    return latest > timezone.now() - timedelta(seconds=JUDGE_RUN_COOLDOWN_SECONDS)


def _judge_completion_count_today(agent: PersistentAgent) -> int:
    now = timezone.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return PersistentAgentCompletion.objects.filter(
        agent=agent,
        completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
        created_at__gte=day_start,
    ).count()


def is_agent_judge_enabled_for_agent(agent: PersistentAgent) -> bool:
    if not getattr(agent, "user_id", None):
        return False

    try:
        Flag = get_waffle_flag_model()
        flag = Flag.objects.get(name=PERSISTENT_AGENT_LLM_JUDGE)
    except ObjectDoesNotExist:
        return False
    except (DatabaseError, ImproperlyConfigured):
        logger.exception(
            "Failed loading waffle flag '%s' when evaluating judge eligibility for agent %s",
            PERSISTENT_AGENT_LLM_JUDGE,
            getattr(agent, "id", None),
        )
        return False

    try:
        return bool(flag.is_active_for_user(agent.user))
    except (DatabaseError, ImproperlyConfigured, AttributeError):
        logger.exception(
            "Error evaluating waffle flag '%s' for user %s (agent %s)",
            PERSISTENT_AGENT_LLM_JUDGE,
            getattr(agent, "user_id", None),
            getattr(agent, "id", None),
        )
        return False


def _trigger_reasons(
    recent_messages: list[PersistentAgentMessage],
    recent_tool_calls: list[PersistentAgentToolCall],
) -> list[str]:
    reasons: list[str] = []
    recent_errors = [call for call in recent_tool_calls if (call.status or "").lower() == "error"]
    if len(recent_errors) >= JUDGE_FAILED_TOOL_THRESHOLD:
        reasons.append("failed_tool_calls")

    if _has_negative_user_language(recent_messages):
        reasons.append("negative_user_language")

    if _has_stonewall_loop(recent_messages):
        reasons.append("stonewall_loop")

    return reasons


def _recent_messages(agent: PersistentAgent) -> list[PersistentAgentMessage]:
    return list(
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("conversation", "from_endpoint")
        .order_by("-timestamp", "-seq")[:JUDGE_RECENT_MESSAGE_LIMIT]
    )


def _recent_tool_calls(agent: PersistentAgent) -> list[PersistentAgentToolCall]:
    return list(
        PersistentAgentToolCall.objects.filter(step__agent=agent)
        .select_related("step")
        .order_by("-step__created_at")[:JUDGE_RECENT_TOOL_LIMIT]
    )


def _has_negative_user_language(messages: list[PersistentAgentMessage]) -> bool:
    text = "\n".join((message.body or "").lower() for message in messages if not message.is_outbound)
    return any(pattern in text for pattern in NEGATIVE_LANGUAGE_PATTERNS)


def _has_stonewall_loop(messages: list[PersistentAgentMessage]) -> bool:
    inbound = [
        _normalize_repeat_text(message.body or "")
        for message in messages
        if not message.is_outbound and message.body
    ][:3]
    if len(inbound) < 3 or len(set(inbound)) > 1:
        return False

    outbound_text = "\n".join((message.body or "").lower() for message in messages if message.is_outbound)
    return any(pattern in outbound_text for pattern in BLOCKER_PATTERNS)


def _normalize_repeat_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = re.sub(r"[^a-z0-9 ]+", "", normalized)
    return normalized[:240]


def _build_trajectory_packet(
    agent: PersistentAgent,
    *,
    tools: list[dict[str, Any]],
    recent_messages: list[PersistentAgentMessage],
    recent_tool_calls: list[PersistentAgentToolCall],
    trigger_reasons: list[str],
    non_judge_step_count: int,
) -> dict[str, Any]:
    tier = get_agent_llm_tier(agent)
    recent_steps = list(
        PersistentAgentStep.objects.filter(agent=agent)
        .exclude(system_step__code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION)
        .order_by("-created_at")[:JUDGE_RECENT_STEP_LIMIT]
    )
    recent_directives = list(
        PersistentAgentSystemMessage.objects.filter(agent=agent)
        .order_by("-created_at")
        .values("id", "body", "created_at", "delivered_at")[:5]
    )
    plan_snapshot = _plan_snapshot(agent)

    return {
        "agent": {
            "id": str(agent.id),
            "name": agent.name or "",
            "current_tier": tier.value,
            "charter": _truncate(agent.charter or "", 1200),
        },
        "trigger_reasons": trigger_reasons,
        "non_judge_step_count": non_judge_step_count,
        "policy_excerpts": [
            "If the agent is blocked or missing a user decision, it should use request_human_input rather than repeating a blocker.",
            "If the user repeats the same command and the agent repeats the same blocker for three turns, reframe the ask prominently or request tracked human input.",
            "If task complexity exceeds the current intelligence tier, suggest an intelligence upgrade instead of silently struggling.",
            "If the current approach is failing, suggest a concrete strategy shift grounded in available tools.",
        ],
        "capability_manifest": _capability_manifest(tools),
        "plan_snapshot": plan_snapshot,
        "recent_messages": [_serialize_message(message) for message in reversed(recent_messages)],
        "recent_tool_calls": [_serialize_tool_call(call) for call in reversed(recent_tool_calls)],
        "recent_steps": [_serialize_step(step) for step in reversed(recent_steps)],
        "recent_system_directives": [_serialize_directive(row) for row in reversed(recent_directives)],
    }


def _plan_snapshot(agent: PersistentAgent) -> dict[str, Any]:
    try:
        snapshot = build_plan_snapshot(agent)
    except (AttributeError, ValueError, RuntimeError):
        return {}
    return {
        "todo_count": getattr(snapshot, "todo_count", 0),
        "doing_count": getattr(snapshot, "doing_count", 0),
        "done_count": getattr(snapshot, "done_count", 0),
        "todo_titles": list(getattr(snapshot, "todo_titles", [])[:5]),
        "doing_titles": list(getattr(snapshot, "doing_titles", [])[:5]),
    }


def _capability_manifest(tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    for tool in tools[:80]:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict):
            continue
        name = _clean_text(fn.get("name"), default="", max_length=160)
        if not name:
            continue
        manifest.append(
            {
                "name": name,
                "description": _clean_text(fn.get("description"), default="", max_length=500),
                "availability": "enabled",
            }
        )
    return manifest


def _serialize_message(message: PersistentAgentMessage) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "direction": "agent_to_user" if message.is_outbound else "user_to_agent",
        "channel": _message_channel(message),
        "timestamp": message.timestamp.isoformat() if message.timestamp else None,
        "body": _truncate(message.body or "", 1200),
    }


def _message_channel(message: PersistentAgentMessage) -> str:
    if getattr(message, "conversation", None) is not None:
        return message.conversation.channel
    if getattr(message, "from_endpoint", None) is not None:
        return message.from_endpoint.channel
    return ""


def _serialize_tool_call(call: PersistentAgentToolCall) -> dict[str, Any]:
    return {
        "step_id": str(call.step_id),
        "tool_name": call.tool_name,
        "status": call.status or "complete",
        "params": call.tool_params if isinstance(call.tool_params, dict) else {},
        "result": _truncate(call.result or "", 1200),
        "created_at": call.step.created_at.isoformat() if call.step and call.step.created_at else None,
    }


def _serialize_step(step: PersistentAgentStep) -> dict[str, Any]:
    return {
        "id": str(step.id),
        "created_at": step.created_at.isoformat() if step.created_at else None,
        "description": _truncate(step.description or "", 1000),
    }


def _serialize_directive(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "body": _truncate(row.get("body") or "", 1000),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "delivered_at": row["delivered_at"].isoformat() if row.get("delivered_at") else None,
    }


def _build_judge_messages(trajectory: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are Gobii's internal trajectory judge. Review the provided agent trajectory and decide whether "
                "the agent needs one concise intervention. You are not the working agent. You cannot execute the "
                "agent's tools. You may call exactly one tool: report_judge_suggestion. Prefer no_action unless the "
                "evidence shows a meaningful quality issue. For intelligence_upgrade, explain why the current tier "
                "appears insufficient and recommend the minimum higher tier that would likely help."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(trajectory, sort_keys=True, default=str),
        },
    ]


def _judge_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": REPORT_TOOL_NAME,
            "description": "Report one advisory judge suggestion for the live chat UI and the agent's next prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestion_type": {
                        "type": "string",
                        "enum": sorted(ALLOWED_SUGGESTION_TYPES),
                    },
                    "title": {"type": "string"},
                    "ui_message": {"type": "string"},
                    "agent_directive": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "object"},
                    "recommended_tier": {"type": "string"},
                },
                "required": [
                    "suggestion_type",
                    "title",
                    "ui_message",
                    "agent_directive",
                    "confidence",
                    "evidence",
                ],
            },
        },
    }


def _extract_report_payload(response: Any) -> dict[str, Any] | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    if not tool_calls:
        return None
    for call in tool_calls:
        function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
        name = function.get("name") if isinstance(function, dict) else getattr(function, "name", None)
        if name != REPORT_TOOL_NAME:
            continue
        raw_args = function.get("arguments") if isinstance(function, dict) else getattr(function, "arguments", None)
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            parsed = json.loads(raw_args or "{}")
            if isinstance(parsed, dict):
                return parsed
    return None


def _format_agent_directive(title: str, agent_directive: str, suggestion_type: str) -> str:
    return (
        "[LLM Judge Suggestion]\n"
        f"Type: {suggestion_type}\n"
        f"Title: {title}\n\n"
        f"{agent_directive}\n\n"
        "Treat this as guidance from Gobii's internal quality judge. Apply it if it is relevant to the current task."
    )


def _default_title(suggestion_type: str) -> str:
    if suggestion_type == PersistentAgentJudgeSuggestion.SuggestionType.INTELLIGENCE_UPGRADE:
        return "Consider higher intelligence"
    if suggestion_type == PersistentAgentJudgeSuggestion.SuggestionType.REQUEST_HUMAN_INPUT:
        return "Ask for the missing decision"
    if suggestion_type == PersistentAgentJudgeSuggestion.SuggestionType.STONEWALL_REFRAME:
        return "Reframe the blocker"
    return "Adjust strategy"


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _clean_choice(value: Any) -> str:
    return str(value or "").strip().lower()


def _clean_text(value: Any, *, default: str, max_length: int) -> str:
    if value is None:
        text = default
    else:
        text = str(value).strip()
    if not text:
        text = default
    return text[:max_length]


def _truncate(value: str, max_length: int) -> str:
    text = str(value or "")
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _judge_run_cache_key(agent: PersistentAgent, evidence_hash: str) -> str:
    return f"agent-judge:run:{agent.id}:{evidence_hash}"


def dismiss_judge_suggestion(suggestion: PersistentAgentJudgeSuggestion) -> None:
    if suggestion.status != PersistentAgentJudgeSuggestion.Status.ACTIVE:
        return
    suggestion.status = PersistentAgentJudgeSuggestion.Status.DISMISSED
    suggestion.resolved_at = timezone.now()
    suggestion.save(update_fields=["status", "resolved_at"])


__all__ = [
    "REPORT_TOOL_NAME",
    "build_manual_judge_trigger",
    "build_judge_trigger",
    "dismiss_judge_suggestion",
    "is_agent_judge_enabled_for_agent",
    "maybe_run_agent_judge",
    "report_judge_suggestion",
    "run_manual_agent_judge",
]
