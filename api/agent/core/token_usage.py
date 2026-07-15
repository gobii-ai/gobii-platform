"""Helpers for extracting token usage and cost data from LiteLLM responses."""

import json
import logging
from decimal import Decimal, InvalidOperation
from numbers import Number
from typing import Any, Optional, Sequence, Tuple
from uuid import UUID

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
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _first_decimal(*values: Any) -> Optional[Decimal]:
    for value in values:
        decimal_value = _safe_decimal(value)
        if decimal_value is not None:
            return decimal_value
    return None


def usage_attribute(usage: Any, attr: str, default: Optional[Any] = None) -> Any:
    if usage is None:
        return default
    if isinstance(usage, dict):
        return usage.get(attr, default)
    return getattr(usage, attr, default)


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

    model = token_usage.get("pricing_model") or token_usage.get("model")
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


def _extract_direct_cost_breakdown(
    response: Any,
    *,
    usage: Optional[Any],
    token_usage: dict[str, Any],
) -> dict:
    containers: list[Any] = []
    if usage is not None:
        containers.append(usage)

    model_extra = getattr(response, "model_extra", None)
    if isinstance(response, dict):
        model_extra = response.get("model_extra")
    if model_extra is not None:
        containers.append(model_extra)

    containers.append(response)

    input_total: Optional[Decimal] = None
    output_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None

    for container in containers:
        cost_details = usage_attribute(container, "cost_details")
        if cost_details is None:
            continue

        if input_total is None:
            input_total = _first_decimal(
                usage_attribute(cost_details, "upstream_inference_prompt_cost"),
                usage_attribute(cost_details, "prompt_cost"),
                usage_attribute(cost_details, "input_cost_total"),
            )
        if output_cost is None:
            output_cost = _first_decimal(
                usage_attribute(cost_details, "upstream_inference_completions_cost"),
                usage_attribute(cost_details, "upstream_inference_completion_cost"),
                usage_attribute(cost_details, "completion_cost"),
                usage_attribute(cost_details, "output_cost"),
            )
        if total_cost is None:
            total_cost = _first_decimal(
                usage_attribute(cost_details, "upstream_inference_cost"),
                usage_attribute(cost_details, "total_cost"),
                usage_attribute(cost_details, "cost"),
            )

    if total_cost is None:
        for container in containers:
            total_cost = _safe_decimal(usage_attribute(container, "cost"))
            if total_cost is not None:
                break

    if total_cost is None and input_total is not None and output_cost is not None:
        total_cost = input_total + output_cost

    if input_total is None and output_cost is None and total_cost is None:
        return {}

    direct_costs: dict[str, Decimal] = {}
    if input_total is not None:
        direct_costs["input_cost_total"] = _quantize_cost(input_total)
    if output_cost is not None:
        direct_costs["output_cost"] = _quantize_cost(output_cost)
    if total_cost is not None:
        direct_costs["total_cost"] = _quantize_cost(total_cost)
    return direct_costs


def _merge_direct_cost_fields(
    token_usage: dict[str, Any],
    direct_cost_fields: dict[str, Decimal],
) -> dict[str, Decimal]:
    merged_costs = dict(direct_cost_fields)
    if "input_cost_total" not in merged_costs:
        return merged_costs
    if "input_cost_uncached" in merged_costs or "input_cost_cached" in merged_costs:
        return merged_costs
    if coerce_int(token_usage.get("cached_tokens")) > 0:
        return merged_costs

    merged_costs["input_cost_uncached"] = merged_costs["input_cost_total"]
    merged_costs["input_cost_cached"] = _quantize_cost(Decimal("0"))
    return merged_costs


def extract_token_usage(
    response: Any,
    *,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    pricing_model: Optional[str] = None,
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
    if pricing_model:
        token_usage["pricing_model"] = pricing_model
    direct_cost_fields = _extract_direct_cost_breakdown(
        response,
        usage=usage,
        token_usage=token_usage,
    )
    if not usage:
        if direct_cost_fields:
            token_usage.update(_merge_direct_cost_fields(token_usage, direct_cost_fields))
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

    cost_fields = direct_cost_fields
    if not cost_fields:
        cost_fields = compute_cost_breakdown(token_usage, usage)
    else:
        cost_fields = _merge_direct_cost_fields(token_usage, cost_fields)
    if cost_fields:
        token_usage.update(cost_fields)

    return token_usage, usage


def _coerce_response_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if value.__class__.__module__ == "unittest.mock":
            return None
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return None


def extract_response_id(response: Any) -> Optional[str]:
    if response is None:
        return None

    if isinstance(response, dict):
        model_extra = response.get("model_extra")
        candidates = [response.get("response_id"), response.get("id")]
    else:
        model_extra = getattr(response, "model_extra", None)
        candidates = [getattr(response, "response_id", None), getattr(response, "id", None)]

    if isinstance(model_extra, dict):
        candidates.extend([model_extra.get("response_id"), model_extra.get("id")])

    for candidate in candidates:
        coerced = _coerce_response_id(candidate)
        if coerced:
            return coerced
    return None


def _coerce_duration_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if value.__class__.__module__ == "unittest.mock":
            return None
    except Exception:
        pass
    try:
        return int(round(float(value)))
    except Exception:
        return None


def extract_request_duration_ms(response: Any) -> Optional[int]:
    if response is None:
        return None

    duration_ms = None
    model_extra = None

    if isinstance(response, dict):
        duration_ms = response.get("request_duration_ms")
        if duration_ms is None:
            duration_ms = response.get("_gobii_request_duration_ms")
        model_extra = response.get("model_extra")
    else:
        duration_ms = getattr(response, "request_duration_ms", None)
        if duration_ms is None:
            duration_ms = getattr(response, "_gobii_request_duration_ms", None)
        model_extra = getattr(response, "model_extra", None)

    if duration_ms is None and isinstance(model_extra, dict):
        duration_ms = model_extra.get("request_duration_ms")
        if duration_ms is None:
            duration_ms = model_extra.get("duration_ms")

    return _coerce_duration_ms(duration_ms)


def extract_time_to_first_token_ms(response: Any) -> Optional[int]:
    if response is None:
        return None

    ttft_ms = None
    model_extra = None

    if isinstance(response, dict):
        ttft_ms = response.get("time_to_first_token_ms")
        if ttft_ms is None:
            ttft_ms = response.get("_gobii_time_to_first_token_ms")
        model_extra = response.get("model_extra")
    else:
        ttft_ms = getattr(response, "time_to_first_token_ms", None)
        if ttft_ms is None:
            ttft_ms = getattr(response, "_gobii_time_to_first_token_ms", None)
        model_extra = getattr(response, "model_extra", None)

    if ttft_ms is None and isinstance(model_extra, dict):
        ttft_ms = model_extra.get("time_to_first_token_ms")
        if ttft_ms is None:
            ttft_ms = model_extra.get("ttft_ms")

    return _coerce_duration_ms(ttft_ms)


def completion_tokens_per_second(completion_tokens: Any, request_duration_ms: Any) -> Optional[float]:
    completion_token_count = coerce_int(completion_tokens)
    duration_ms = _coerce_duration_ms(request_duration_ms)
    if completion_token_count is None or completion_token_count <= 0 or duration_ms is None or duration_ms <= 0:
        return None
    return round(completion_token_count / (duration_ms / 1000), 2)


def completion_metadata_from_response(
    response: Any,
    *,
    response_id: Optional[str] = None,
    request_duration_ms: Optional[int] = None,
    time_to_first_token_ms: Optional[int] = None,
) -> dict:
    resolved_response_id = response_id or extract_response_id(response)
    resolved_duration_ms = (
        request_duration_ms if request_duration_ms is not None else extract_request_duration_ms(response)
    )
    resolved_time_to_first_token_ms = (
        time_to_first_token_ms
        if time_to_first_token_ms is not None
        else extract_time_to_first_token_ms(response)
    )

    metadata: dict[str, Any] = {}
    if resolved_response_id is not None:
        metadata["response_id"] = resolved_response_id
    if resolved_duration_ms is not None:
        metadata["request_duration_ms"] = resolved_duration_ms
    if resolved_time_to_first_token_ms is not None:
        metadata["time_to_first_token_ms"] = resolved_time_to_first_token_ms
    return metadata


def completion_kwargs_from_usage(
    token_usage: Optional[dict],
    *,
    completion_type: str,
    response: Any = None,
    response_id: Optional[str] = None,
    request_duration_ms: Optional[int] = None,
    time_to_first_token_ms: Optional[int] = None,
) -> dict:
    base = {"completion_type": completion_type}
    metadata = completion_metadata_from_response(
        response,
        response_id=response_id,
        request_duration_ms=request_duration_ms,
        time_to_first_token_ms=time_to_first_token_ms,
    )
    if metadata:
        base.update(metadata)
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


def _stringify_prompt_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(content)


def _split_prompt_messages(messages: Sequence[dict[str, Any]]) -> tuple[str, str]:
    system_parts: list[str] = []
    conversation_parts: list[tuple[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "user").strip().lower()
        content = _stringify_prompt_content(message.get("content"))
        if role in {"system", "developer"}:
            system_parts.append(content)
        else:
            conversation_parts.append((role, content))

    if len(conversation_parts) == 1 and conversation_parts[0][0] == "user":
        user_prompt = conversation_parts[0][1]
    else:
        user_prompt = "\n\n".join(
            f"{role.title()}:\n{content}"
            for role, content in conversation_parts
        )
    return "\n\n".join(system_parts), user_prompt


def _archive_completion_prompt(
    *,
    agent: Any,
    completion_type: str,
    token_usage: Optional[dict],
    prompt_messages: Optional[Sequence[dict[str, Any]]],
    prompt_text: Optional[str],
) -> Optional[UUID]:
    if not prompt_messages and not prompt_text:
        return None

    from api.services.prompt_archives import archive_agent_prompt

    system_prompt, user_prompt = _split_prompt_messages(prompt_messages or [])
    if prompt_text:
        user_prompt = prompt_text
    prompt_tokens = coerce_int((token_usage or {}).get("prompt_tokens"))
    return archive_agent_prompt(
        agent=agent,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        tokens_before=prompt_tokens,
        tokens_after=prompt_tokens,
        tokens_saved=0,
        token_budget=0,
        extra_payload={"completion_type": completion_type},
    )[3]


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
    pricing_model: Optional[str] = None,
    response_id: Optional[str] = None,
    request_duration_ms: Optional[int] = None,
    time_to_first_token_ms: Optional[int] = None,
    prompt_messages: Optional[Sequence[dict[str, Any]]] = None,
    prompt_text: Optional[str] = None,
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
                pricing_model=pricing_model,
            )
            if derived_token_usage is None:
                derived_token_usage = extracted_token_usage
        except Exception:
            logger.debug("Failed to extract token usage from response", exc_info=True)

        if thinking_content is None:
            thinking_content = extract_reasoning_content(response)

    if derived_token_usage is None:
        derived_token_usage = {"model": model, "provider": provider}
        if pricing_model:
            derived_token_usage["pricing_model"] = pricing_model

    resolved_eval_run_id = eval_run_id or _get_budget_eval_run_id()
    try:
        from ...services.billing_snapshot import get_billing_snapshot_for_owner
        from ...services.owner_execution_pause import resolve_agent_owner
        from ...models import PersistentAgentCompletion  # local import to avoid cycles

        billing_snapshot = get_billing_snapshot_for_owner(resolve_agent_owner(agent))
        prompt_archive_id = _archive_completion_prompt(
            agent=agent,
            completion_type=completion_type,
            token_usage=derived_token_usage,
            prompt_messages=prompt_messages,
            prompt_text=prompt_text,
        )
        PersistentAgentCompletion.objects.create(
            agent=agent,
            eval_run_id=resolved_eval_run_id,
            prompt_archive_id=prompt_archive_id,
            thinking_content=thinking_content,
            **billing_snapshot,
            **completion_kwargs_from_usage(
                derived_token_usage,
                completion_type=completion_type,
                response=response,
                response_id=response_id,
                request_duration_ms=request_duration_ms,
                time_to_first_token_ms=time_to_first_token_ms,
            ),
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
    "completion_metadata_from_response",
    "completion_kwargs_from_usage",
    "completion_tokens_per_second",
    "extract_request_duration_ms",
    "extract_time_to_first_token_ms",
    "extract_response_id",
    "extract_reasoning_content",
    "extract_token_usage",
    "usage_attribute",
]
