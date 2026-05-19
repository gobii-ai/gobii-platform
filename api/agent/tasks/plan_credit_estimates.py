"""Celery task for estimating persistent-agent plan credit usage."""

import logging

from celery import shared_task
from django.utils import timezone
import litellm

from api.agent.core.llm_config import LLMNotConfiguredError, get_summarization_llm_configs
from api.agent.core.llm_utils import EmptyLiteLLMResponseError, InvalidLiteLLMResponseError, run_completion
from api.agent.core.token_usage import log_agent_completion
from api.models import PersistentAgentCompletion, PersistentAgentPlanCreditEstimate
from api.services.plan_credit_estimates import (
    build_estimator_messages,
    determine_frequency,
    estimate_tool,
    extract_estimate_arguments,
    heuristic_estimate_payload,
    is_current_plan_estimate,
    normalize_estimate_payload,
)

logger = logging.getLogger(__name__)
_LITELLM_ESTIMATOR_ERROR_NAMES = (
    "APIConnectionError",
    "APIError",
    "APIResponseValidationError",
    "AuthenticationError",
    "BadGatewayError",
    "BadRequestError",
    "BudgetExceededError",
    "ContentPolicyViolationError",
    "ContextWindowExceededError",
    "InternalServerError",
    "JSONSchemaValidationError",
    "NotFoundError",
    "OpenAIError",
    "PermissionDeniedError",
    "RateLimitError",
    "RouterRateLimitError",
    "ServiceUnavailableError",
    "Timeout",
    "UnprocessableEntityError",
    "UnsupportedParamsError",
)
_LITELLM_ESTIMATOR_ERRORS = tuple(
    error_cls
    for error_cls in (getattr(litellm, name, None) for name in _LITELLM_ESTIMATOR_ERROR_NAMES)
    if isinstance(error_cls, type)
)
_ESTIMATOR_RECOVERABLE_ERRORS = _LITELLM_ESTIMATOR_ERRORS + (
    EmptyLiteLLMResponseError,
    InvalidLiteLLMResponseError,
    ValueError,
    TypeError,
    KeyError,
    IndexError,
)


def _broadcast_if_current(estimate: PersistentAgentPlanCreditEstimate) -> None:
    if not is_current_plan_estimate(estimate):
        return
    try:
        from console.agent_chat.signals import broadcast_plan_estimate_update

        broadcast_plan_estimate_update(estimate)
    except (ImportError, RuntimeError):
        logger.warning("Unable to broadcast plan credit estimate %s", estimate.id, exc_info=True)


def _save_complete_if_pending(
    estimate: PersistentAgentPlanCreditEstimate,
    payload: dict,
    *,
    llm_model: str = "",
    llm_provider: str = "",
    error_message: str = "",
) -> PersistentAgentPlanCreditEstimate | None:
    now = timezone.now()
    updated = PersistentAgentPlanCreditEstimate.objects.filter(
        id=estimate.id,
        status=PersistentAgentPlanCreditEstimate.Status.PENDING,
    ).update(
        status=PersistentAgentPlanCreditEstimate.Status.COMPLETE,
        frequency=payload["frequency"],
        base_estimate=payload["base_estimate"],
        step_estimates=payload["step_estimates"],
        tool_breakdown=payload["tool_breakdown"],
        assumptions=payload["assumptions"],
        llm_model=llm_model[:256],
        llm_provider=llm_provider[:128],
        error_message=error_message[:1000],
        generated_at=now,
        updated_at=now,
    )
    if not updated:
        return None
    estimate.refresh_from_db()
    return estimate


def _generate_llm_estimate(estimate: PersistentAgentPlanCreditEstimate) -> tuple[dict, str, str]:
    configs = get_summarization_llm_configs(agent=estimate.agent)
    last_error = ""
    tool = estimate_tool()
    messages = build_estimator_messages(estimate)
    for provider, model, params in configs:
        try:
            response = run_completion(
                model=model,
                messages=messages,
                params=params,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "provide_plan_credit_estimate"}},
                drop_params=True,
            )
            log_agent_completion(
                estimate.agent,
                completion_type=PersistentAgentCompletion.CompletionType.PLAN_CREDIT_ESTIMATE,
                response=response,
                model=model,
                provider=provider,
            )
            raw_payload = extract_estimate_arguments(response)
            normalized = normalize_estimate_payload(raw_payload, estimate.frequency)
            return normalized, model, provider
        except _ESTIMATOR_RECOVERABLE_ERRORS as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Plan credit estimate failed for %s via %s",
                estimate.id,
                model,
                exc_info=True,
            )
    raise ValueError(last_error or "No estimator model returned a valid estimate.")


@shared_task(bind=True, name="api.agent.tasks.estimate_plan_credit_usage")
def estimate_plan_credit_usage_task(self, estimate_id: str) -> None:  # noqa: ARG001
    try:
        estimate = (
            PersistentAgentPlanCreditEstimate.objects.select_related("agent", "kanban_event")
            .get(id=estimate_id)
        )
    except PersistentAgentPlanCreditEstimate.DoesNotExist:
        logger.info("Skipping plan credit estimate; estimate %s no longer exists", estimate_id)
        return

    if estimate.status != PersistentAgentPlanCreditEstimate.Status.PENDING:
        return

    estimate.frequency = determine_frequency(estimate.agent.schedule)
    estimate.save(update_fields=["frequency", "updated_at"])

    try:
        payload, model, provider = _generate_llm_estimate(estimate)
        error_message = ""
    except LLMNotConfiguredError as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        payload = heuristic_estimate_payload(estimate, error_message)
        model = ""
        provider = ""
    except ValueError as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        payload = heuristic_estimate_payload(estimate, error_message)
        model = ""
        provider = ""

    updated_estimate = _save_complete_if_pending(
        estimate,
        payload,
        llm_model=model,
        llm_provider=provider,
        error_message=error_message,
    )
    if updated_estimate is not None:
        _broadcast_if_current(updated_estimate)
