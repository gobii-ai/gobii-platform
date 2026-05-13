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

from api.agent.core.llm_config import INPUT_TOKEN_HEADROOM, LLMNotConfiguredError, get_agent_judge_llm_config, get_agent_llm_tier
from api.agent.core.llm_utils import run_completion
from api.agent.core.prompt_context import _create_token_estimator
from api.agent.core.promptree import Prompt
from api.agent.core.token_usage import log_agent_completion
from api.agent.tools.plan import build_plan_snapshot
from api.services.prompt_settings import get_prompt_settings
from api.models import (
    LLMRoutingProfile,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentJudgeSuggestion,
    PersistentAgentMessage,
    PersistentAgentSkill,
    PersistentAgentStep,
    PersistentAgentSystemSkillState,
    PersistentAgentSystemMessage,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)
from constants.feature_flags import PERSISTENT_AGENT_LLM_JUDGE

logger = logging.getLogger(__name__)

JUDGE_FAILED_TOOL_THRESHOLD = 3
JUDGE_TRIGGER_TOOL_LIMIT = 12
JUDGE_TRIGGER_MESSAGE_LIMIT = 12
JUDGE_RECENT_STEP_LIMIT = 80
JUDGE_RECENT_DIRECTIVE_LIMIT = 20
JUDGE_SQLITE_CONTEXT_CHARS = 30000
JUDGE_TOOL_RESULT_CONTEXT_CHARS = 100000
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


@dataclass(frozen=True)
class JudgePromptLimits:
    prompt_token_budget: int
    message_history_limit: int
    tool_call_history_limit: int
    skill_prompt_limit: int
    enabled_tool_limit: int


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
    return _run_judge(agent, trigger, cache_evidence=False, review_required=True)


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
            "message_ids": [str(message.id) for message in recent_messages],
            "tool_step_ids": [str(call.step_id) for call in recent_tool_calls],
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
            "message_ids": [str(message.id) for message in recent_messages],
            "tool_step_ids": [str(call.step_id) for call in recent_tool_calls],
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


def _run_judge(
    agent: PersistentAgent,
    trigger: JudgeTrigger,
    *,
    cache_evidence: bool = True,
    review_required: bool = False,
) -> dict[str, Any]:
    if cache_evidence:
        cache.set(_judge_run_cache_key(agent, trigger.evidence_hash), True, timeout=JUDGE_RUN_CACHE_TTL_SECONDS)

    try:
        config = get_agent_judge_llm_config()
    except LLMNotConfiguredError:
        logger.info("Skipping agent judge for %s because no LLM config is available.", agent.id)
        return {"ran": False, "status": "llm_not_configured"}

    tool_def = _judge_tool_definition()
    provider, model, params = config
    prompt_limits = _judge_prompt_limits()
    messages = _build_judge_messages(trigger.trajectory, model=model, prompt_limits=prompt_limits)
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
    completion = (
        PersistentAgentCompletion.objects.filter(
            agent=agent,
            completion_type=PersistentAgentCompletion.CompletionType.LLM_JUDGE,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    payload = _extract_report_payload(response)
    if payload is None:
        logger.info("Agent judge for %s returned no report tool call.", agent.id)
        return _judge_run_result(
            status="missing_report_tool_call",
            payload=None,
            suggestion=None,
            completion=completion,
        )
    suggestion = report_judge_suggestion(agent, trigger, payload, review_required=review_required)
    suggestion_type = _clean_choice(payload.get("suggestion_type")) or None
    return {
        "ran": True,
        "status": "completed",
        "suggestion_type": suggestion_type,
        "suggestion": _serialize_judge_result_suggestion(suggestion, completion),
    }


def _judge_run_result(
    *,
    status: str,
    payload: dict[str, Any] | None,
    suggestion: PersistentAgentJudgeSuggestion | None,
    completion: PersistentAgentCompletion | None,
) -> dict[str, Any]:
    return {
        "ran": True,
        "status": status,
        "suggestion_type": _clean_choice((payload or {}).get("suggestion_type")) or None,
        "suggestion": _serialize_judge_result_suggestion(suggestion, completion),
    }


def _serialize_judge_result_suggestion(
    suggestion: PersistentAgentJudgeSuggestion | None,
    completion: PersistentAgentCompletion | None,
) -> dict[str, Any] | None:
    if suggestion is None:
        return None
    return {
        "id": str(suggestion.id),
        "suggestionId": str(suggestion.id),
        "suggestionType": suggestion.suggestion_type,
        "title": suggestion.title,
        "message": suggestion.ui_message,
        "agentDirective": suggestion.agent_directive,
        "recommendedTier": suggestion.recommended_tier or None,
        "confidence": suggestion.confidence,
        "status": suggestion.status,
        "createdAt": suggestion.created_at.isoformat() if suggestion.created_at else None,
        "reasoning": (completion.thinking_content if completion else None) or "",
        "completionId": str(completion.id) if completion else None,
    }


def report_judge_suggestion(
    agent: PersistentAgent,
    trigger: JudgeTrigger,
    payload: dict[str, Any],
    *,
    review_required: bool = False,
) -> PersistentAgentJudgeSuggestion | None:
    suggestion_type = _clean_choice(payload.get("suggestion_type"))
    if suggestion_type not in ALLOWED_SUGGESTION_TYPES:
        suggestion_type = PersistentAgentJudgeSuggestion.SuggestionType.STRATEGY_SHIFT
    if suggestion_type == NO_ACTION:
        return None

    if PersistentAgentJudgeSuggestion.objects.filter(
        agent=agent,
        suggestion_type=suggestion_type,
        evidence_hash=trigger.evidence_hash,
    ).exists():
        return None

    title = _default_title(suggestion_type)
    ui_message = _clean_text(payload.get("message") or payload.get("ui_message"), default=title, max_length=1200)
    agent_directive = _clean_text(payload.get("agent_directive"), default=ui_message, max_length=2000)
    recommended_tier = ""
    if suggestion_type == PersistentAgentJudgeSuggestion.SuggestionType.INTELLIGENCE_UPGRADE:
        recommended_tier = _clean_text(payload.get("recommended_tier"), default="", max_length=64)

    try:
        with transaction.atomic():
            step = PersistentAgentStep.objects.create(
                agent=agent,
                description=f"LLM judge suggestion ({suggestion_type}): {title}\n{agent_directive}",
            )
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION,
                notes=f"evidence_hash={trigger.evidence_hash}",
            )
            system_message = PersistentAgentSystemMessage.objects.create(
                agent=agent,
                body=_format_agent_directive(title, agent_directive, suggestion_type),
                is_active=not review_required,
            )
            return PersistentAgentJudgeSuggestion.objects.create(
                agent=agent,
                suggestion_type=suggestion_type,
                title=title,
                ui_message=ui_message,
                agent_directive=agent_directive,
                confidence=0,
                recommended_tier=recommended_tier,
                evidence={},
                trigger_reasons=trigger.reasons,
                evidence_hash=trigger.evidence_hash,
                status=(
                    PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW
                    if review_required
                    else PersistentAgentJudgeSuggestion.Status.ACTIVE
                ),
                source_step=step,
                system_message=system_message,
            )
    except IntegrityError:
        return None


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


def _judge_prompt_limits() -> JudgePromptLimits:
    settings = get_prompt_settings()
    budget = settings.ultra_max_prompt_token_budget
    endpoint_limit = _agent_judge_endpoint_max_input_tokens()
    if endpoint_limit is not None:
        budget = min(budget, max(1, endpoint_limit - INPUT_TOKEN_HEADROOM))

    return JudgePromptLimits(
        prompt_token_budget=max(1, budget),
        message_history_limit=max(1, settings.ultra_max_message_history_limit),
        tool_call_history_limit=max(1, settings.ultra_max_tool_call_history_limit),
        skill_prompt_limit=max(0, settings.ultra_max_skill_prompt_limit),
        enabled_tool_limit=max(1, settings.ultra_max_enabled_tool_limit),
    )


def _agent_judge_endpoint_max_input_tokens() -> int | None:
    try:
        profile = (
            LLMRoutingProfile.objects.filter(is_active=True, is_eval_snapshot=False)
            .select_related("agent_judge_endpoint")
            .first()
        )
    except DatabaseError:
        logger.debug("Unable to resolve active routing profile for judge prompt limit.", exc_info=True)
        return None
    endpoint = getattr(profile, "agent_judge_endpoint", None) if profile is not None else None
    if endpoint is None:
        return None
    return endpoint.max_input_tokens


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
    trigger_tool_calls = recent_tool_calls[:JUDGE_TRIGGER_TOOL_LIMIT]
    trigger_messages = recent_messages[:JUDGE_TRIGGER_MESSAGE_LIMIT]
    recent_errors = [call for call in trigger_tool_calls if (call.status or "").lower() == "error"]
    if len(recent_errors) >= JUDGE_FAILED_TOOL_THRESHOLD:
        reasons.append("failed_tool_calls")

    if _has_negative_user_language(trigger_messages):
        reasons.append("negative_user_language")

    if _has_stonewall_loop(trigger_messages):
        reasons.append("stonewall_loop")

    return reasons


def _recent_messages(agent: PersistentAgent) -> list[PersistentAgentMessage]:
    limit = _judge_prompt_limits().message_history_limit
    return list(
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .select_related("conversation", "from_endpoint")
        .order_by("-timestamp", "-seq")[:limit]
    )


def _recent_tool_calls(agent: PersistentAgent) -> list[PersistentAgentToolCall]:
    limit = _judge_prompt_limits().tool_call_history_limit
    return list(
        PersistentAgentToolCall.objects.filter(step__agent=agent)
        .select_related("step")
        .order_by("-step__created_at")[:limit]
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
        .select_related("system_step")
        .exclude(system_step__code=PersistentAgentSystemStep.Code.LLM_JUDGE_SUGGESTION)
        .order_by("-created_at")[:JUDGE_RECENT_STEP_LIMIT]
    )
    recent_directives = list(
        PersistentAgentSystemMessage.objects.filter(agent=agent)
        .order_by("-created_at")
        .values("id", "body", "created_at", "delivered_at", "is_active")[:JUDGE_RECENT_DIRECTIVE_LIMIT]
    )
    plan_snapshot = _plan_snapshot(agent)
    current_context = _build_current_context_snapshot(agent)

    return {
        "agent": {
            "id": str(agent.id),
            "name": agent.name or "",
            "current_tier": tier.value,
            "charter": _truncate(agent.charter or "", 1200),
        },
        "packet_notes": [
            "This is a generic trajectory-debug packet for an advisory judge.",
            "All chronology arrays are oldest-to-newest within their retained windows.",
            "Recent trajectory may include the current processing run and prior runs; use timestamps, system_step_code, and message direction to avoid overclaiming run boundaries.",
            "Distinguish directly observed facts from inferred causes. If a cause is not directly visible, phrase it as uncertainty.",
        ],
        "trigger_reasons": trigger_reasons,
        "non_judge_step_count": non_judge_step_count,
        "policy_excerpts": [
            "If the agent is blocked or missing a user decision, it should use request_human_input rather than repeating a blocker.",
            "If the user repeats the same command and the agent repeats the same blocker for three turns, reframe the ask prominently or request tracked human input.",
            "If task complexity exceeds the current intelligence tier, suggest an intelligence upgrade instead of silently struggling.",
            "If the current approach is failing, suggest a concrete strategy shift grounded in available tools.",
        ],
        "capability_manifest": _capability_manifest(tools),
        "current_context": current_context,
        "recent_trajectory": {
            "plan_snapshot": plan_snapshot,
            "messages": [_serialize_message(message) for message in reversed(recent_messages)],
            "tool_calls": [_serialize_tool_call(call) for call in reversed(recent_tool_calls)],
            "steps": [_serialize_step(step) for step in reversed(recent_steps)],
            "system_directives": [_serialize_directive(row) for row in reversed(recent_directives)],
        },
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


def _build_current_context_snapshot(agent: PersistentAgent) -> dict[str, Any]:
    return {
        "skills": _skill_context(agent),
        "sqlite": _sqlite_context_snapshot(),
    }


def _skill_context(agent: PersistentAgent) -> dict[str, Any]:
    limit = _judge_prompt_limits().skill_prompt_limit
    if limit <= 0:
        return {
            "saved_skills": [],
            "enabled_system_skills": [],
        }

    latest_skills = list(
        PersistentAgentSkill.objects.filter(agent=agent)
        .order_by("name", "-version", "-updated_at")
    )
    latest_by_name: dict[str, PersistentAgentSkill] = {}
    for skill in latest_skills:
        if skill.name not in latest_by_name:
            latest_by_name[skill.name] = skill

    saved_skills = sorted(
        latest_by_name.values(),
        key=lambda row: (
            row.last_used_at is not None,
            row.last_used_at or row.updated_at,
            row.updated_at,
        ),
        reverse=True,
    )[:limit]
    system_skills = list(
        PersistentAgentSystemSkillState.objects.filter(agent=agent, is_enabled=True)
        .order_by("-last_used_at", "-enabled_at")[:limit]
    )
    return {
        "saved_skills": [
            {
                "source_type": "agent_skill",
                "name": skill.name,
                "description": _truncate(skill.description or "", 1000),
                "version": skill.version,
                "tools": skill.tools if isinstance(skill.tools, list) else [],
                "instructions": _truncate(skill.instructions or "", 3000),
                "last_used_at": skill.last_used_at.isoformat() if skill.last_used_at else None,
                "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
            }
            for skill in saved_skills
        ],
        "enabled_system_skills": [
            {
                "source_type": "system_skill_state",
                "skill_key": state.skill_key,
                "last_used_at": state.last_used_at.isoformat() if state.last_used_at else None,
                "enabled_at": state.enabled_at.isoformat() if state.enabled_at else None,
                "usage_count": state.usage_count,
            }
            for state in system_skills
        ],
    }


def _sqlite_context_snapshot() -> dict[str, Any]:
    try:
        from api.agent.tools.sqlite_state import get_sqlite_digest_prompt, get_sqlite_schema_prompt

        return {
            "source_type": "sqlite_context",
            "schema": _truncate(get_sqlite_schema_prompt(), JUDGE_SQLITE_CONTEXT_CHARS),
            "digest": _truncate(get_sqlite_digest_prompt(), JUDGE_SQLITE_CONTEXT_CHARS),
        }
    except (OSError, RuntimeError, ValueError, DatabaseError):
        logger.debug("Unable to build judge SQLite context snapshot.", exc_info=True)
        return {
            "source_type": "sqlite_context",
            "schema": "",
            "digest": "",
            "error": "unavailable",
        }


def _capability_manifest(tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    limit = _judge_prompt_limits().enabled_tool_limit
    for tool in tools[:limit]:
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
        "source_type": "message",
        "trajectory_scope": "recent",
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
        "source_type": "tool_call",
        "trajectory_scope": "recent",
        "step_id": str(call.step_id),
        "tool_name": call.tool_name,
        "status": call.status or "complete",
        "params": call.tool_params if isinstance(call.tool_params, dict) else {},
        "result": _truncate(call.result or "", JUDGE_TOOL_RESULT_CONTEXT_CHARS),
        "created_at": call.step.created_at.isoformat() if call.step and call.step.created_at else None,
    }


def _serialize_step(step: PersistentAgentStep) -> dict[str, Any]:
    system_step = getattr(step, "system_step", None)
    return {
        "source_type": "step",
        "trajectory_scope": "recent",
        "id": str(step.id),
        "created_at": step.created_at.isoformat() if step.created_at else None,
        "system_step_code": system_step.code if system_step is not None else None,
        "system_step_notes": _truncate(system_step.notes or "", 1000) if system_step is not None else None,
        "description": _truncate(step.description or "", 1000),
    }


def _serialize_directive(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_type": "system_directive",
        "trajectory_scope": "recent",
        "id": str(row.get("id")),
        "body": _truncate(row.get("body") or "", 1000),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "delivered_at": row["delivered_at"].isoformat() if row.get("delivered_at") else None,
        "is_active": bool(row.get("is_active")),
    }


def _judge_system_prompt() -> str:
    return (
        "You are Gobii's internal trajectory judge. Review the provided agent trajectory and decide whether "
        "the agent needs one concise intervention. You are not the working agent. You cannot execute the "
        "agent's tools. You may call exactly one tool: report_judge_suggestion. Prefer no_action unless the "
        "evidence shows a meaningful quality issue. Keep message short. For intelligence_upgrade, recommend "
        "the minimum higher tier that would likely help. Base suggestions only on evidence in the packet. "
        "Separate directly observed facts from inferred causes; when a cause is uncertain, say so instead "
        "of presenting it as fact. Prefer concrete operational guidance over diagnosis."
    )


def _json_section(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, indent=2)


def _build_judge_user_prompt(
    trajectory: dict[str, Any],
    *,
    model: str,
    prompt_limits: JudgePromptLimits,
) -> str:
    prompt = Prompt(token_estimator=_create_token_estimator(model))
    recent_trajectory = trajectory.get("recent_trajectory") or {}
    current_context = trajectory.get("current_context") or {}

    prompt.section_text(
        "judge_contract",
        _json_section(
            {
                "trigger_reasons": trajectory.get("trigger_reasons") or [],
                "policy_excerpts": trajectory.get("policy_excerpts") or [],
                "packet_notes": trajectory.get("packet_notes") or [],
                "output_contract": (
                    "Call exactly one tool named report_judge_suggestion. Use no_action when the evidence "
                    "does not justify intervention."
                ),
            }
        ),
        weight=8,
        non_shrinkable=True,
    )
    prompt.section_text(
        "agent",
        _json_section(
            {
                "agent": trajectory.get("agent") or {},
                "non_judge_step_count": trajectory.get("non_judge_step_count"),
            }
        ),
        weight=8,
        non_shrinkable=True,
    )

    high_priority = prompt.group("high_priority", weight=8)
    high_priority.section_text("messages", _json_section(recent_trajectory.get("messages") or []), weight=8)
    high_priority.section_text("system_directives", _json_section(recent_trajectory.get("system_directives") or []), weight=5)
    high_priority.section_text("plan_snapshot", _json_section(recent_trajectory.get("plan_snapshot") or {}), weight=4)
    high_priority.section_text("steps", _json_section(recent_trajectory.get("steps") or []), weight=4)

    medium_priority = prompt.group("medium_priority", weight=5)
    medium_priority.section_text("tool_calls", _json_section(recent_trajectory.get("tool_calls") or []), weight=6)
    medium_priority.section_text("skills", _json_section((current_context.get("skills") or {})), weight=3)
    medium_priority.section_text("capability_manifest", _json_section(trajectory.get("capability_manifest") or []), weight=2)

    low_priority = prompt.group("low_priority", weight=2)
    low_priority.section_text("sqlite", _json_section(current_context.get("sqlite") or {}), weight=2)

    return prompt.render(prompt_limits.prompt_token_budget)


def _build_judge_messages(
    trajectory: dict[str, Any],
    *,
    model: str,
    prompt_limits: JudgePromptLimits,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _judge_system_prompt(),
        },
        {
            "role": "user",
            "content": _build_judge_user_prompt(
                trajectory,
                model=model,
                prompt_limits=prompt_limits,
            ),
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
                    "message": {
                        "type": "string",
                        "description": "Short explanation for humans and audit records.",
                    },
                    "agent_directive": {
                        "type": "string",
                        "description": "Concrete one-shot instruction for the working agent. Optional for no_action.",
                    },
                    "recommended_tier": {"type": "string"},
                },
                "required": [
                    "suggestion_type",
                    "message",
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
            try:
                parsed = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                return None
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


def approve_judge_suggestion(suggestion: PersistentAgentJudgeSuggestion) -> None:
    if suggestion.status == PersistentAgentJudgeSuggestion.Status.ACTIVE:
        return
    if suggestion.status != PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW:
        return
    system_message = suggestion.system_message
    if system_message is not None and not system_message.is_active:
        system_message.is_active = True
        system_message.save(update_fields=["is_active"])
    suggestion.status = PersistentAgentJudgeSuggestion.Status.ACTIVE
    suggestion.resolved_at = timezone.now()
    suggestion.save(update_fields=["status", "resolved_at"])


def dismiss_judge_suggestion(suggestion: PersistentAgentJudgeSuggestion) -> None:
    if suggestion.status not in {
        PersistentAgentJudgeSuggestion.Status.ACTIVE,
        PersistentAgentJudgeSuggestion.Status.PENDING_REVIEW,
    }:
        return
    system_message = suggestion.system_message
    if system_message is not None and system_message.is_active:
        system_message.is_active = False
        system_message.save(update_fields=["is_active"])
    suggestion.status = PersistentAgentJudgeSuggestion.Status.DISMISSED
    suggestion.resolved_at = timezone.now()
    suggestion.save(update_fields=["status", "resolved_at"])


__all__ = [
    "REPORT_TOOL_NAME",
    "approve_judge_suggestion",
    "build_manual_judge_trigger",
    "build_judge_trigger",
    "dismiss_judge_suggestion",
    "is_agent_judge_enabled_for_agent",
    "maybe_run_agent_judge",
    "report_judge_suggestion",
    "run_manual_agent_judge",
]
