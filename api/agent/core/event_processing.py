"""
Event processing entry‑point for persistent agents.

This module provides the core logic for processing agent events, including
incoming messages, cron triggers, and other events. It handles the main agent
loop with LLM‑powered reasoning and tool execution using tiered failover.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from decimal import Decimal
from numbers import Number
from typing import List, Tuple, Union, Optional, Dict, Any
from uuid import UUID

import litellm
from opentelemetry import baggage, trace
from pottery import Redlock
from django.db import transaction, close_old_connections
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
from .processing_flags import clear_processing_queued_flag
from .llm_utils import run_completion
from ..short_description import (
    maybe_schedule_mini_description,
    maybe_schedule_short_description,
)
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
from .prompt_context import (
    build_prompt_context,
    get_agent_daily_credit_state,
    get_agent_tools,
)

from ..tools.email_sender import execute_send_email
from ..tools.sms_sender import execute_send_sms
from ..tools.search_web import execute_search_web
from ..tools.spawn_web_task import execute_spawn_web_task
from ..tools.schedule_updater import execute_update_schedule
from ..tools.charter_updater import execute_update_charter
from ..tools.database_enabler import execute_enable_database
from ..tools.sqlite_state import agent_sqlite_db
from ..tools.secure_credentials_request import execute_secure_credentials_request
from ..tools.request_contact_permission import execute_request_contact_permission
from ..tools.search_tools import execute_search_tools
from ..tools.tool_manager import execute_enabled_tool
from ..tools.web_chat_sender import execute_send_chat_message
from ..tools.peer_dm import execute_send_agent_message
from ..tools.webhook_sender import execute_send_webhook_event
from ...models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentCompletion,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    PersistentAgentPromptArchive,
)
from config import settings
from config.redis_client import get_redis_client
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from .gemini_cache import (
    GEMINI_CACHE_BLOCKLIST,
    GeminiCachedContentManager,
    disable_gemini_cache_for,
    is_gemini_cache_conflict_error,
)

logger = logging.getLogger(__name__)

_COST_PRECISION = Decimal("0.000001")

def _quantize_cost(value: Decimal) -> Decimal:
    return value.quantize(_COST_PRECISION)


def _safe_decimal(value: Optional[float]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


def _usage_attribute(usage: Any, attr: str, default: Optional[Any] = None) -> Any:
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(attr, default)
    return getattr(usage, attr, default)


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Number):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if isinstance(value, str):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    try:
        return int(str(value))
    except Exception:
        return 0


def _compute_cost_breakdown(token_usage: Optional[dict], raw_usage: Optional[Any]) -> dict:
    if not token_usage:
        return {}

    model = token_usage.get("model")
    provider = token_usage.get("provider")
    pricing_provider_hint = token_usage.get("pricing_provider_hint")
    if not model:
        return {}

    prompt_tokens = _coerce_int(token_usage.get("prompt_tokens"))
    completion_tokens = _coerce_int(token_usage.get("completion_tokens"))
    cached_tokens = _coerce_int(token_usage.get("cached_tokens"))

    if raw_usage is not None and not cached_tokens:
        details = _usage_attribute(raw_usage, "prompt_tokens_details")
        if details:
            if isinstance(details, dict):
                cached_tokens = _coerce_int(details.get("cached_tokens"))
            else:
                cached_tokens = _coerce_int(_usage_attribute(details, "cached_tokens", 0))

    cached_tokens = min(cached_tokens, prompt_tokens)
    uncached_tokens = max(prompt_tokens - cached_tokens, 0)

    model_variants = [model]
    provider_from_model: Optional[str] = None
    if "/" in model:
        provider_from_model, stripped_model = model.split("/", 1)
        model_variants.append(stripped_model)
    provider_candidates: List[Optional[str]] = []
    if pricing_provider_hint:
        provider_candidates.append(pricing_provider_hint)
    if provider_from_model:
        provider_candidates.append(provider_from_model)
    if provider and provider not in provider_candidates:
        provider_candidates.append(provider)
    provider_candidates.append(None)

    model_info = None
    for candidate_model in model_variants:
        for candidate_provider in provider_candidates:
            try:
                model_info = litellm.get_model_info(
                    model=candidate_model,
                    custom_llm_provider=candidate_provider,
                )
            except Exception:
                logger.debug(
                    "Failed to get model info from litellm for model=%s provider=%s",
                    candidate_model,
                    candidate_provider,
                    exc_info=True,
                )
                model_info = None
                
            if model_info:
                break
        if model_info:
            break

    if not model_info:
        logger.debug(
            "Unable to resolve LiteLLM pricing for model=%s provider_hint=%s",
            model,
            provider,
        )
        return {}

    def _info_value(key: str) -> Optional[float]:
        if isinstance(model_info, dict):
            return model_info.get(key)
        return getattr(model_info, key, None)

    input_price = _safe_decimal(_info_value("input_cost_per_token"))
    cache_read_price = _safe_decimal(_info_value("cache_read_input_token_cost")) or input_price
    output_price = _safe_decimal(_info_value("output_cost_per_token"))

    if input_price is None and output_price is None:
        return {}

    zero = Decimal("0")
    uncached_cost = (input_price or zero) * Decimal(uncached_tokens)
    cached_cost = (cache_read_price or zero) * Decimal(cached_tokens)
    input_total = uncached_cost + cached_cost
    output_cost = (output_price or zero) * Decimal(completion_tokens)
    total_cost = input_total + output_cost

    return {
        "input_cost_total": _quantize_cost(input_total),
        "input_cost_uncached": _quantize_cost(uncached_cost),
        "input_cost_cached": _quantize_cost(cached_cost),
        "output_cost": _quantize_cost(output_cost),
        "total_cost": _quantize_cost(total_cost),
    }
tracer = trace.get_tracer("gobii.utils")

MAX_AGENT_LOOP_ITERATIONS = 100
ARG_LOG_MAX_CHARS = 500
RESULT_LOG_MAX_CHARS = 500
AUTO_SLEEP_FLAG = "auto_sleep_ok"
PREFERRED_PROVIDER_MAX_AGE = timedelta(hours=1)

__all__ = ["process_agent_events"]

def _attempt_cycle_close_for_sleep(agent: PersistentAgent, budget_ctx: Optional[BudgetContext]) -> None:
    """Best-effort attempt to close the budget cycle when the agent goes idle."""

    if budget_ctx is None:
        return

    # If a pending follow-up is queued, keep the cycle open so it can run
    try:
        redis_client = get_redis_client()
        pending_key = f"agent-event-processing:pending:{agent.id}"
        if redis_client.get(pending_key):
            logger.info(
                "Agent %s sleeping with pending follow-up flag; keeping cycle active.",
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
    
    # Charter length
    if agent.charter:
        total_length += len(agent.charter)
    
    # Rough estimates for other content
    # History: estimate based on recent steps and comms
    recent_steps = (
        PersistentAgentStep.objects.filter(agent=agent)
        .select_related("tool_call")
        .only("description", "tool_call__result")
        .order_by('-created_at')[:10]
    )
    for step in recent_steps:
        # Add description length
        if step.description:
            total_length += len(step.description)
        
        # Add tool call result if this step has one
        try:
            if step.tool_call and step.tool_call.result:
                total_length += len(str(step.tool_call.result))
        except PersistentAgentToolCall.DoesNotExist:
            # This step doesn't have a tool call, which is fine
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


_GEMINI_CACHE_MANAGER = GeminiCachedContentManager()
_GEMINI_CACHE_BLOCKLIST = GEMINI_CACHE_BLOCKLIST


def _completion_with_failover(
    messages: List[dict], 
    tools: List[dict], 
    failover_configs: List[Tuple[str, str, dict]],
    agent_id: str = None,
    safety_identifier: str = None,
    preferred_config: Optional[Tuple[str, str]] = None,
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
        
    Returns:
        Tuple of (LiteLLM completion response, token usage dict)
        Token usage dict contains: prompt_tokens, completion_tokens, total_tokens, 
        cached_tokens (optional), model, provider
        
    Raises:
        Exception: If all providers in all tiers fail
    """
    last_exc: Exception | None = None
    base_messages: List[dict] = list(messages or [])
    base_tools: List[dict] = list(tools or [])
    
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
                pricing_provider_hint = params_base.pop("pricing_provider_hint", None)
                endpoint_key = params_base.pop("endpoint_key", None)

                if pricing_provider_hint and "custom_llm_provider" not in params_base:
                    params_base["custom_llm_provider"] = pricing_provider_hint

                if endpoint_key:
                    llm_span.set_attribute("llm.endpoint_key", endpoint_key)
                if pricing_provider_hint:
                    llm_span.set_attribute("llm.pricing_provider_hint", pricing_provider_hint)

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

                # Record usage if available and prepare token usage dict
                token_usage: Optional[dict] = {
                    "model": model,
                    "provider": provider,
                }
                if pricing_provider_hint:
                    token_usage["pricing_provider_hint"] = pricing_provider_hint
                if endpoint_key:
                    token_usage["endpoint_key"] = endpoint_key
                usage = response.model_extra.get("usage", None)
                if usage:
                    llm_span.set_attribute("llm.usage.prompt_tokens", usage.prompt_tokens)
                    llm_span.set_attribute("llm.usage.completion_tokens", usage.completion_tokens)
                    llm_span.set_attribute("llm.usage.total_tokens", usage.total_tokens)
                    
                    # Build token usage dict to return
                    token_usage.update(
                        {
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "total_tokens": usage.total_tokens,
                        }
                    )
                    
                    details = usage.prompt_tokens_details
                    if details:
                        cached_tokens = getattr(details, "cached_tokens", None) or 0
                        llm_span.set_attribute("llm.usage.cached_tokens", cached_tokens)
                        if cached_tokens:
                            token_usage["cached_tokens"] = cached_tokens

                    cost_fields = _compute_cost_breakdown(token_usage, usage)
                    if cost_fields:
                        token_usage.update(cost_fields)

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
            PersistentAgentCompletion.objects.filter(agent=agent)
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
) -> bool | Decimal:
    """Ensure the agent's owner has a task credit and consume it just-in-time.

    Returns True if execution may proceed; False if insufficient or consumption fails.
    In failure cases, this function records a step + system step and logging.
    """
    if tool_name == "send_chat_message":
        return True

    if not settings.GOBII_PROPRIETARY_MODE or not getattr(agent, "user_id", None):
        return True

    owner_user = agent.user
    cost: Decimal | None = None
    consumed: dict | None = None

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
            available = TaskCreditService.get_user_task_credits_available(owner_user)
        except Exception as e:
            logger.error(
                "Credit availability check (in-loop) failed for agent %s (user %s): %s",
                agent.id,
                owner_user.id,
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
            consumed = TaskCreditService.check_and_consume_credit(owner_user, amount=cost)
    except Exception as e:
        logger.error(
            "Credit consumption (in-loop) failed for agent %s (user %s): %s",
            agent.id,
            owner_user.id,
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

    if cost is not None:
        try:
            updated_state = get_agent_daily_credit_state(agent)
        except Exception as exc:
            logger.error(
                "Failed to refresh daily credit usage for agent %s: %s",
                agent.id,
                exc,
            )
        else:
            if credit_snapshot is not None:
                credit_snapshot["daily_state"] = updated_state
                credit_snapshot.pop("available", None)
            if span is not None:
                try:
                    remaining_after = updated_state.get("hard_limit_remaining")
                    span.set_attribute(
                        "credit_check.daily_remaining_after",
                        float(remaining_after) if remaining_after is not None else -1.0,
                    )
                except Exception:
                    pass

    return cost if cost is not None else True


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def process_agent_events(
    persistent_agent_id: Union[str, UUID],
    budget_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    depth: Optional[int] = None,
) -> None:
    """Process all outstanding events for a persistent agent."""
    span = trace.get_current_span()
    baggage.set_baggage("persistent_agent.id", str(persistent_agent_id))
    span.set_attribute("persistent_agent.id", str(persistent_agent_id))

    logger.info("process_agent_events(%s) called", persistent_agent_id)

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
    )
    set_budget_context(ctx)

    # Use distributed lock to ensure only one event processing call per agent
    lock_key = f"agent-event-processing:{persistent_agent_id}"
    pending_key = f"agent-event-processing:pending:{persistent_agent_id}"

    redis_client = get_redis_client()
    lock = Redlock(key=lock_key, masters={redis_client}, auto_release_time=14400)  # 4 hour timeout to match Celery

    lock_acquired = False
    processed_agent: Optional[PersistentAgent] = None

    try:
        # Try to acquire the lock with a small timeout. If this instance cannot get the lock
        # we record a *pending* flag in Redis so that exactly one follow-up task is queued
        # once the current lock holder finishes.
        if not lock.acquire(blocking=True, timeout=1):
            # Mark that another round of processing should run once the lock is released.
            # The TTL prevents a stale flag from surviving indefinitely in the unlikely event
            # that no task is ever scheduled to clear it (e.g., if the agent is deleted).
            redis_client.set(pending_key, "1", ex=600)

            logger.info(
                "Skipping event processing for agent %s – another process is already handling events (flagged pending)",
                persistent_agent_id,
            )
            span.add_event("Event processing skipped – lock acquisition failed (pending flag set)")
            span.set_attribute("lock.acquired", False)
            clear_processing_queued_flag(persistent_agent_id)
            return

        lock_acquired = True
        clear_processing_queued_flag(persistent_agent_id)

        logger.info("Acquired distributed lock for agent %s", persistent_agent_id)
        span.add_event("Distributed lock acquired")
        span.set_attribute("lock.acquired", True)

        # ---------------- SQLite state context ---------------- #
        with agent_sqlite_db(str(persistent_agent_id)) as _sqlite_db_path:
            # Optional: record path for debugging (will be in temp dir)
            span.set_attribute("sqlite_db.temp_path", _sqlite_db_path)

            # Actual event processing logic (protected by the lock)
            processed_agent = _process_agent_events_locked(persistent_agent_id, span)

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
        should_schedule_follow_up = False

        # Only the lock holder attempts release & follow-up scheduling.
        if lock_acquired:
            try:
                lock.release()
                logger.info("Released distributed lock for agent %s", persistent_agent_id)
                span.add_event("Distributed lock released")
            except Exception as e:
                logger.warning("Failed to release lock for agent %s: %s", persistent_agent_id, str(e))
                span.add_event("Lock release warning")
            finally:
                # If any skipped task set the *pending* flag while we were processing, enqueue a single
                # follow-up task now and clear the flag. This provides a simple *debounce* mechanism so
                # that no matter how many additional triggers happened while we were running, we only
                # schedule one more round of processing.
                deleted_count = redis_client.delete(pending_key)
                # Redis returns the number of keys removed (0 or 1). When tests mock the client,
                # this may be a MagicMock instance – treat that as 0 to avoid eager-execution loops.
                if isinstance(deleted_count, int) and deleted_count > 0:
                    should_schedule_follow_up = True

        if should_schedule_follow_up:
            logger.info("Scheduling follow-up event processing for agent %s due to pending flag", persistent_agent_id)
            span.add_event("Follow-up task scheduled")
            # Import inside function to avoid circular dependency at module import time
            try:
                from ..tasks.process_events import process_agent_events_task  # noqa: WPS433 (runtime import)

                # Skip follow-up if cycle is closed/exhausted
                status = AgentBudgetManager.get_cycle_status(agent_id=str(persistent_agent_id))
                active_id = AgentBudgetManager.get_active_budget_id(agent_id=str(persistent_agent_id))
                if status and (status != "active" or active_id != ctx.budget_id):
                    logger.info(
                        "Skipping follow-up scheduling for agent %s; cycle status=%s active_id=%s ctx_id=%s",
                        persistent_agent_id,
                        status,
                        active_id,
                        ctx.budget_id,
                    )
                else:
                    # Propagate budget context to follow‑up tasks
                    process_agent_events_task.delay(
                        str(persistent_agent_id),
                        budget_id=ctx.budget_id,
                        branch_id=ctx.branch_id,
                        depth=ctx.depth,
                    )
            except Exception as e:
                logger.error(
                    "Failed to schedule follow-up event processing for agent %s: %s", persistent_agent_id, str(e)
                )

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


def _process_agent_events_locked(persistent_agent_id: Union[str, UUID], span) -> Optional[PersistentAgent]:
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

        try:
            maybe_schedule_short_description(agent)
        except Exception:
            logger.exception(
                "Failed to evaluate short description scheduling for agent %s",
                persistent_agent_id,
            )

        try:
            maybe_schedule_mini_description(agent)
        except Exception:
            logger.exception(
                "Failed to evaluate mini description scheduling for agent %s",
                persistent_agent_id,
            )
        try:
            maybe_schedule_agent_tags(agent)
        except Exception:
            logger.exception(
                "Failed to evaluate tag scheduling for agent %s",
                persistent_agent_id,
            )

        if settings.GOBII_PROPRIETARY_MODE:
            owner_user = getattr(agent, "user", None)
            if owner_user is not None:
                try:
                    available = TaskCreditService.get_user_task_credits_available(owner_user)
                except Exception as e:
                    # Defensive: if availability calc fails, log and proceed (do not block agent)
                    logger.error(
                        "Credit availability check failed for agent %s (user %s): %s",
                        persistent_agent_id,
                        owner_user.id,
                        str(e),
                    )
                    available = None

                span.set_attribute("credit_check.available", int(available) if available is not None else 0)
                span.set_attribute("credit_check.proprietary_mode", True)

                if available is not None and available != TASKS_UNLIMITED and available <= 0:
                    msg = f"Skipped processing due to insufficient credits (proprietary mode)."
                    logger.warning(
                        "Persistent agent %s not processed – user %s has no remaining task credits.",
                        persistent_agent_id,
                        owner_user.id,
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

                    span.add_event("Agent processing skipped - daily credit limit reached")
                    span.set_attribute("credit_check.daily_limit_block", True)
                    return agent
            else:
                # Agents without a linked user (system/automation) are not gated
                span.add_event("Agent has no linked user; skipping credit gate")
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

    for i in range(max_remaining):
        with tracer.start_as_current_span(f"Agent Loop Iteration {i + 1}"):
            iter_span = trace.get_current_span()
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
            history, fitted_token_count, prompt_archive_id = build_prompt_context(
                agent,
                current_iteration=i + 1,
                max_iterations=MAX_AGENT_LOOP_ITERATIONS,
                reasoning_only_streak=reasoning_only_streak,
                is_first_run=is_first_run,
                daily_credit_state=credit_snapshot["daily_state"] if credit_snapshot else None,
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

            def _token_usage_fields(token_usage: Optional[dict]) -> dict:
                """Return sanitized token usage values for step creation."""
                if not token_usage:
                    return {}
                return {
                    "prompt_tokens": token_usage.get("prompt_tokens"),
                    "completion_tokens": token_usage.get("completion_tokens"),
                    "total_tokens": token_usage.get("total_tokens"),
                    "cached_tokens": token_usage.get("cached_tokens"),
                    "llm_model": token_usage.get("model"),
                    "llm_provider": token_usage.get("provider"),
                    "input_cost_total": token_usage.get("input_cost_total"),
                    "input_cost_uncached": token_usage.get("input_cost_uncached"),
                    "input_cost_cached": token_usage.get("input_cost_cached"),
                    "output_cost": token_usage.get("output_cost"),
                    "total_cost": token_usage.get("total_cost"),
                }
            
            # Use the fitted token count from promptree for LLM selection
            # This fixes the bug where we were using joined message token count
            # which could exceed thresholds even when fitted content was under limits
            logger.debug(
                "Using fitted token count %d for agent %s LLM selection",
                fitted_token_count,
                agent.id
            )
            
            # Select provider tiers based on the fitted token count
            try:
                failover_configs = get_llm_config_with_failover(
                    agent_id=str(agent.id),
                    token_count=fitted_token_count,
                    agent=agent,
                    is_first_loop=is_first_run,
                )
            except LLMNotConfiguredError:
                logger.warning(
                    "Agent %s loop aborted – LLM configuration missing mid-run.",
                    agent.id,
                )
                span.add_event("Agent loop aborted - llm bootstrap required")
                break

            preferred_config = _get_recent_preferred_config(agent=agent, run_sequence_number=run_sequence_number)

            try:
                response, token_usage = _completion_with_failover(
                    messages=history,
                    tools=tools,
                    failover_configs=failover_configs,
                    agent_id=str(agent.id),
                    safety_identifier=agent.user.id if agent.user else None,
                    preferred_config=preferred_config,
                )
                
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

            msg = response.choices[0].message
            token_usage_fields = _token_usage_fields(token_usage)
            completion: Optional[PersistentAgentCompletion] = None

            def _ensure_completion() -> PersistentAgentCompletion:
                nonlocal completion
                if completion is None:
                    completion = PersistentAgentCompletion.objects.create(
                        agent=agent,
                        **token_usage_fields,
                    )
                return completion

            def _attach_completion(step_kwargs: dict) -> None:
                completion_obj = _ensure_completion()
                step_kwargs["completion"] = completion_obj

            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                if msg.content:
                    logger.info("Agent %s reasoning: %s", agent.id, msg.content)
                    step_kwargs = {
                        "agent": agent,
                        "description": f"Internal reasoning: {msg.content[:500]}",
                    }
                    _attach_completion(step_kwargs)
                    step = PersistentAgentStep.objects.create(**step_kwargs)
                    _attach_prompt_archive(step)
                reasoning_only_streak += 1
                continue

            reasoning_only_streak = 0

            reasoning_text = (msg.content or "").strip()
            if reasoning_text:
                response_description = f"Internal reasoning: {reasoning_text[:500]}"
            else:
                try:
                    tool_count = len(tool_calls)
                except TypeError:
                    tool_count = 0
                response_description = (
                    f"LLM response issued {tool_count} tool call(s)."
                    if tool_count
                    else "LLM response issued tool calls."
                )
            response_step_kwargs = {
                "agent": agent,
                "description": response_description,
            }
            _attach_completion(response_step_kwargs)
            response_step = PersistentAgentStep.objects.create(**response_step_kwargs)
            _attach_prompt_archive(response_step)

            # Log high-level summary of tool calls
            try:
                logger.info(
                    "Agent %s: model returned %d tool_call(s)",
                    agent.id,
                    len(tool_calls) if isinstance(tool_calls, list) else 0,
                )
                for idx, call in enumerate(list(tool_calls) or [], start=1):
                    try:
                        fn_name = getattr(getattr(call, "function", None), "name", None) or (
                            call.get("function", {}).get("name") if isinstance(call, dict) else None
                        )
                        raw_args = getattr(getattr(call, "function", None), "arguments", None) or (
                            call.get("function", {}).get("arguments") if isinstance(call, dict) else ""
                        )
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

            all_calls_sleep = True
            executed_calls = 0
            followup_required = False
            try:
                tool_names = [
                    (
                        getattr(getattr(c, "function", None), "name", None)
                        or (c.get("function", {}).get("name") if isinstance(c, dict) else None)
                    )
                    for c in (tool_calls or [])
                ]
                has_non_sleep_calls = any(name != "sleep_until_next_trigger" for name in tool_names)
                actionable_calls_total = sum(
                    1 for name in tool_names if name != "sleep_until_next_trigger"
                )
            except Exception:
                # Defensive fallback: assume we have actionable work so the agent keeps processing
                has_non_sleep_calls = True
                actionable_calls_total = len(tool_calls or []) if tool_calls else 0

            for idx, call in enumerate(tool_calls, start=1):
                with tracer.start_as_current_span("Execute Tool") as tool_span:
                    tool_span.set_attribute("persistent_agent.id", str(agent.id))
                    tool_name = getattr(getattr(call, "function", None), "name", None)
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
                        credits_consumed = _ensure_credit_for_tool(
                            agent,
                            tool_name,
                            span=tool_span,
                            credit_snapshot=credit_snapshot,
                        )
                        if not credits_consumed:
                            return cumulative_token_usage
                        # Create sleep step with token usage if available
                        step_kwargs = {
                            "agent": agent,
                            "description": "Decided to sleep until next trigger.",
                            "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                        logger.info("Agent %s: sleep_until_next_trigger recorded (will sleep after batch)", agent.id)
                        continue

                    all_calls_sleep = False
                    # Ensure credit is available and consume just-in-time for actionable tools
                    credits_consumed = _ensure_credit_for_tool(
                        agent,
                        tool_name,
                        span=tool_span,
                        credit_snapshot=credit_snapshot,
                    )
                    if not credits_consumed:
                        # Credit insufficient or consumption failed; halt processing
                        return cumulative_token_usage
                    try:
                        raw_args = getattr(call.function, "arguments", "") or ""
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
                    tool_span.set_attribute("tool.params", json.dumps(tool_params))
                    logger.info("Agent %s: %s params=%s", agent.id, tool_name, json.dumps(tool_params)[:ARG_LOG_MAX_CHARS])

                    # Ensure a fresh DB connection before tool execution and subsequent ORM writes
                    close_old_connections()

                    logger.info("Agent %s: executing %s now", agent.id, tool_name)
                    if tool_name == "spawn_web_task":
                        # Delegate recursion gating to execute_spawn_web_task which reads fresh branch depth from Redis
                        result = execute_spawn_web_task(agent, tool_params)
                    elif tool_name == "send_email":
                        result = execute_send_email(agent, tool_params)
                    elif tool_name == "send_sms":
                        result = execute_send_sms(agent, tool_params)
                    elif tool_name == "send_chat_message":
                        result = execute_send_chat_message(agent, tool_params)
                    elif tool_name == "send_agent_message":
                        result = execute_send_agent_message(agent, tool_params)
                    elif tool_name == "send_webhook_event":
                        result = execute_send_webhook_event(agent, tool_params)
                    elif tool_name == "update_schedule":
                        result = execute_update_schedule(agent, tool_params)
                    elif tool_name == "update_charter":
                        result = execute_update_charter(agent, tool_params)
                    elif tool_name == "search_web":
                        result = execute_search_web(agent, tool_params)
                    elif tool_name == "secure_credentials_request":
                        result = execute_secure_credentials_request(agent, tool_params)
                    elif tool_name == "enable_database":
                        result = execute_enable_database(agent, tool_params)
                    elif tool_name == "request_contact_permission":
                        result = execute_request_contact_permission(agent, tool_params)
                    elif tool_name == "search_tools":
                        result = execute_search_tools(agent, tool_params)
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
                        result = execute_enabled_tool(agent, tool_name, tool_params)

                    result_content = json.dumps(result)
                    # Log result summary
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

                    # Guard ORM writes against stale connections; retry once on OperationalError
                    close_old_connections()
                    try:
                        # Create tool step with the execution result preview
                        step_kwargs = {
                            "agent": agent,
                            "description": f"Tool call: {tool_name}({tool_params}) -> {result_content[:100]}",
                            "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                        PersistentAgentToolCall.objects.create(
                            step=step,
                            tool_name=tool_name,
                            tool_params=tool_params,
                            result=result_content,
                        )
                        try:
                            from console.agent_chat.signals import emit_tool_call_realtime  # noqa: WPS433

                            emit_tool_call_realtime(step)
                        except Exception:  # pragma: no cover - defensive logging
                            logger.debug(
                                "Failed to broadcast realtime tool call for agent %s step %s",
                                agent.id,
                                getattr(step, "id", None),
                                exc_info=True,
                            )
                        logger.info("Agent %s: persisted tool call step_id=%s for %s", agent.id, getattr(step, 'id', None), tool_name)
                    except OperationalError:
                        close_old_connections()
                        # Create tool step (retry path)
                        step_kwargs = {
                            "agent": agent,
                            "description": f"Tool call: {tool_name}({tool_params}) -> {result_content[:100]}",
                            "credits_cost": credits_consumed if isinstance(credits_consumed, Decimal) else None,
                        }
                        _attach_completion(step_kwargs)
                        step = PersistentAgentStep.objects.create(**step_kwargs)
                        _attach_prompt_archive(step)
                        PersistentAgentToolCall.objects.create(
                            step=step,
                            tool_name=tool_name,
                            tool_params=tool_params,
                            result=result_content,
                        )
                        try:
                            from console.agent_chat.signals import emit_tool_call_realtime  # noqa: WPS433

                            emit_tool_call_realtime(step)
                        except Exception:  # pragma: no cover - defensive logging
                            logger.debug(
                                "Failed to broadcast realtime tool call (retry) for agent %s step %s",
                                agent.id,
                                getattr(step, "id", None),
                                exc_info=True,
                            )
                        logger.info("Agent %s: persisted tool call (retry) step_id=%s for %s", agent.id, getattr(step, 'id', None), tool_name)
                    allow_auto_sleep = isinstance(result, dict) and result.get(AUTO_SLEEP_FLAG) is True
                    tool_requires_followup = not allow_auto_sleep

                    if tool_requires_followup:
                        followup_required = True

                    executed_calls += 1

            if all_calls_sleep:
                logger.info("Agent %s is sleeping.", agent.id)
                _attempt_cycle_close_for_sleep(agent, budget_ctx)
                return cumulative_token_usage
            elif (
                not followup_required
                and executed_calls > 0
                and executed_calls >= actionable_calls_total
            ):
                logger.info(
                    "Agent %s: tool batch complete with no follow-up required; auto-sleeping.",
                    agent.id,
                )
                _attempt_cycle_close_for_sleep(agent, budget_ctx)
                return cumulative_token_usage
            else:
                logger.info(
                    "Agent %s: executed %d/%d tool_call(s) this iteration",
                    agent.id,
                    executed_calls,
                    len(tool_calls),
                )

    else:
        logger.warning("Agent %s reached max iterations.", agent.id)
    
    return cumulative_token_usage
