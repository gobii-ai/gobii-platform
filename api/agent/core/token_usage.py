"""Helpers for extracting token usage and cost data from LiteLLM responses."""

import json
import logging
from decimal import Decimal
from numbers import Number
from typing import Any, Optional, Tuple

import litellm
from opentelemetry import trace

tracer = trace.get_tracer("gobii.utils")

logger = logging.getLogger(__name__)

# Lazy import to avoid cycles: budget context is only needed when persisting
# completions and should not be loaded at module import time.
def _get_budget_eval_run_id() -> Optional[str]:
    try:
        from .budget import get_current_context

        ctx = get_current_context()
        return getattr(ctx, "eval_run_id", None) if ctx else None
    except Exception:
        logger.debug("Unable to read budget context for eval_run_id", exc_info=True)
        return None

_COST_PRECISION = Decimal("0.000001")


def _quantize_cost(value: Decimal) -> Decimal:
    return value.quantize(_COST_PRECISION)


def _safe_decimal(value: Optional[float]) -> Optional[Decimal]:
    if value is None:
        return None
    return Decimal(str(value))


def usage_attribute(usage: Any, attr: str, default: Optional[Any] = None) -> Any:
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(attr, default)
    return getattr(usage, attr, default)


def _clean_provider(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = str(value).strip()
        except Exception:
            return None
    return text or None


def _is_openrouter_provider(value: Optional[str]) -> bool:
    if not value:
        return False
    return str(value).lower().startswith("openrouter")


def _extract_header_value(headers: Any, header_name: str) -> Optional[str]:
    if not isinstance(headers, dict):
        return None
    header_name = header_name.lower()
    for key, value in headers.items():
        if str(key).lower() == header_name:
            return _clean_provider(value)
    return None


def _normalize_provider_detail(value: Any, provider_hint: Optional[str]) -> Optional[str]:
    candidate = _clean_provider(value)
    if not candidate:
        return None
    if candidate.lower() == "openrouter":
        return None
    if provider_hint and candidate.lower() == str(provider_hint).lower():
        return None
    return candidate


def _resolve_openrouter_provider_detail(
    response: Any,
    *,
    provider_hint: Optional[str],
) -> Optional[str]:
    model_extra = getattr(response, "model_extra", None)
    headers = usage_attribute(model_extra, "response_headers") or usage_attribute(model_extra, "headers")
    header_provider = _extract_header_value(headers, "x-openrouter-provider")
    normalized = _normalize_provider_detail(header_provider, provider_hint)
    if normalized:
        return normalized

    headers = getattr(response, "_response_headers", None)
    header_provider = _extract_header_value(headers, "x-openrouter-provider")
    normalized = _normalize_provider_detail(header_provider, provider_hint)
    if normalized:
        return normalized

    headers = usage_attribute(response, "response_headers") or usage_attribute(response, "headers")
    header_provider = _extract_header_value(headers, "x-openrouter-provider")
    return _normalize_provider_detail(header_provider, provider_hint)


def coerce_int(value: Any) -> int:
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


def compute_cost_breakdown(token_usage: Optional[dict], raw_usage: Optional[Any]) -> dict:
    if not token_usage:
        return {}

    model = token_usage.get("model")
    provider = token_usage.get("provider")
    if not model:
        return {}

    prompt_tokens = coerce_int(token_usage.get("prompt_tokens"))
    completion_tokens = coerce_int(token_usage.get("completion_tokens"))
    cached_tokens = coerce_int(token_usage.get("cached_tokens"))

    if raw_usage is not None and not cached_tokens:
        details = usage_attribute(raw_usage, "prompt_tokens_details")
        if details:
            if isinstance(details, dict):
                cached_tokens = coerce_int(details.get("cached_tokens"))
            else:
                cached_tokens = coerce_int(usage_attribute(details, "cached_tokens", 0))

    cached_tokens = min(cached_tokens, prompt_tokens)
    uncached_tokens = max(prompt_tokens - cached_tokens, 0)

    model_variants = [model]
    provider_from_model: Optional[str] = None
    if "/" in model:
        provider_from_model, stripped_model = model.split("/", 1)
        model_variants.append(stripped_model)
    provider_candidates: list[Optional[str]] = []
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


def extract_token_usage(
    response: Any,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[Any]]:
    if response is None:
        return None, None

    usage = None
    model_extra = getattr(response, "model_extra", None)
    if isinstance(model_extra, dict):
        usage = model_extra.get("usage")
    elif model_extra is not None:
        usage = usage_attribute(model_extra, "usage")

    if usage is None:
        usage = usage_attribute(response, "usage")

    resolved_model = model or usage_attribute(response, "model")
    if resolved_model is None and usage is not None:
        resolved_model = usage_attribute(usage, "model")

    resolved_provider = provider or usage_attribute(response, "provider")
    if resolved_provider is None and usage is not None:
        resolved_provider = usage_attribute(usage, "provider")

    token_usage: dict[str, Any] = {"model": resolved_model, "provider": resolved_provider}
    if not usage:
        return token_usage, None

    prompt_tokens = usage_attribute(usage, "prompt_tokens")
    completion_tokens = usage_attribute(usage, "completion_tokens")
    total_tokens = usage_attribute(usage, "total_tokens")

    if prompt_tokens is not None:
        token_usage["prompt_tokens"] = coerce_int(prompt_tokens)
    if completion_tokens is not None:
        token_usage["completion_tokens"] = coerce_int(completion_tokens)
    if total_tokens is not None:
        token_usage["total_tokens"] = coerce_int(total_tokens)

    details = usage_attribute(usage, "prompt_tokens_details")
    cached_tokens = None
    if details:
        cached_tokens = usage_attribute(details, "cached_tokens")
        if cached_tokens is not None:
            token_usage["cached_tokens"] = coerce_int(cached_tokens)

    cost_fields = compute_cost_breakdown(token_usage, usage)
    if cost_fields:
        token_usage.update(cost_fields)

    if _is_openrouter_provider(resolved_provider):
        provider_detail = _resolve_openrouter_provider_detail(
            response,
            provider_hint=resolved_provider,
        )
        if provider_detail:
            token_usage["provider_detail"] = provider_detail

    return token_usage, usage


def completion_kwargs_from_usage(token_usage: Optional[dict], *, completion_type: str) -> dict:
    base = {"completion_type": completion_type}
    if not token_usage:
        return base
    return {
        **base,
        "prompt_tokens": token_usage.get("prompt_tokens"),
        "completion_tokens": token_usage.get("completion_tokens"),
        "total_tokens": token_usage.get("total_tokens"),
        "cached_tokens": token_usage.get("cached_tokens"),
        "llm_model": token_usage.get("model"),
        "llm_provider": token_usage.get("provider"),
        "llm_provider_detail": token_usage.get("provider_detail"),
        "input_cost_total": token_usage.get("input_cost_total"),
        "input_cost_uncached": token_usage.get("input_cost_uncached"),
        "input_cost_cached": token_usage.get("input_cost_cached"),
        "output_cost": token_usage.get("output_cost"),
        "total_cost": token_usage.get("total_cost"),
    }


def _normalise_reasoning_content(raw: Any) -> Optional[str]:
    if raw is None:
        return None

    if isinstance(raw, str):
        return raw

    if isinstance(raw, list):
        parts: list[str] = []
        for part in raw:
            normalised = _normalise_reasoning_content(part)
            if normalised:
                parts.append(normalised)
        if parts:
            return "\n".join(parts)
        return None

    if isinstance(raw, dict):
        for key in ("text", "content", "output_text"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
        try:
            return json.dumps(raw)
        except Exception:
            return str(raw)

    try:
        return str(raw)
    except Exception:
        return None


def _extract_reasoning_from_message_content(content: Any) -> Optional[str]:
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if isinstance(part_type, str) and part_type.lower() in {"reasoning", "thinking"}:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)

    if parts:
        return "\n".join(parts)
    return None


def extract_reasoning_content(response: Any) -> Optional[str]:
    """Return any reasoning/thinking content from a LiteLLM response."""
    try:
        choices = getattr(response, "choices", None)
        if not choices:
            return None

        first_choice = choices[0]
        message = first_choice.get("message") if isinstance(first_choice, dict) else getattr(first_choice, "message", None)
        if message is None:
            return None

        reasoning_raw: Any
        if isinstance(message, dict):
            reasoning_raw = message.get("reasoning_content")
        else:
            reasoning_raw = getattr(message, "reasoning_content", None)
            if reasoning_raw is None and isinstance(getattr(message, "__dict__", None), dict):
                # Some LiteLLM message objects only expose extra fields via __dict__ even when attribute access fails.
                reasoning_raw = message.__dict__.get("reasoning_content")

        if reasoning_raw is None and hasattr(message, "model_dump"):
            try:
                dumped = message.model_dump()
                if isinstance(dumped, dict):
                    reasoning_raw = dumped.get("reasoning_content")
            except Exception:
                logger.debug("Failed to pull reasoning_content from model_dump", exc_info=True)

        if reasoning_raw is None:
            if isinstance(message, dict):
                content = message.get("content")
            else:
                content = getattr(message, "content", None)
            reasoning_raw = _extract_reasoning_from_message_content(content)

        return _normalise_reasoning_content(reasoning_raw)
    except Exception:
        logger.debug("Failed to extract reasoning content from response", exc_info=True)
        return None


def set_usage_span_attributes(span, usage: Any) -> None:
    if not span or not usage:
        return
    try:
        span.set_attribute("llm.usage.prompt_tokens", coerce_int(usage_attribute(usage, "prompt_tokens")))
        span.set_attribute("llm.usage.completion_tokens", coerce_int(usage_attribute(usage, "completion_tokens")))
        span.set_attribute("llm.usage.total_tokens", coerce_int(usage_attribute(usage, "total_tokens")))
        details = usage_attribute(usage, "prompt_tokens_details")
        if details:
            span.set_attribute("llm.usage.cached_tokens", coerce_int(usage_attribute(details, "cached_tokens")))
    except Exception:
        logger.debug("Failed to set usage span attributes", exc_info=True)


def log_agent_completion(
    agent: Any,
    token_usage: Optional[dict] = None,
    *,
    completion_type: str,
    eval_run_id: Optional[str] = None,
    thinking_content: Optional[str] = None,
    response: Any = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[Any]]:
    """
    Persist an agent completion, optionally deriving token usage and thinking content from a LiteLLM response.

    Returns the token_usage dict and raw usage object (if extracted) so callers
    can re-use them (e.g., for span attributes) without re-parsing the response.
    """
    if agent is None:
        return None, None

    extracted_usage = None
    derived_token_usage = token_usage

    if response is not None:
        try:
            extracted_token_usage, extracted_usage = extract_token_usage(
                response,
                model=model,
                provider=provider,
            )
            if derived_token_usage is None:
                derived_token_usage = extracted_token_usage
        except Exception:
            logger.debug("Failed to extract token usage from response", exc_info=True)

        if thinking_content is None:
            thinking_content = extract_reasoning_content(response)

    if derived_token_usage is None:
        derived_token_usage = {"model": model, "provider": provider}

    resolved_eval_run_id = eval_run_id or _get_budget_eval_run_id()
    try:
        from ...models import PersistentAgentCompletion  # local import to avoid cycles

        PersistentAgentCompletion.objects.create(
            agent=agent,
            eval_run_id=resolved_eval_run_id,
            thinking_content=thinking_content,
            **completion_kwargs_from_usage(derived_token_usage, completion_type=completion_type),
        )
    except Exception as exc:
        logger.warning(
            "Failed to persist completion (type=%s) for agent %s: %s",
            completion_type,
            getattr(agent, "id", None),
            exc,
            exc_info=True,
        )

    return derived_token_usage, extracted_usage


__all__ = [
    "coerce_int",
    "compute_cost_breakdown",
    "completion_kwargs_from_usage",
    "extract_reasoning_content",
    "extract_token_usage",
    "usage_attribute",
]
