import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Sequence

from celery.schedules import crontab, schedule as celery_schedule
from django.db import connection
from django.utils import timezone

from api.agent.core.schedule_parser import ScheduleParser
from api.models import (
    HistoricalAgentCostSample,
    PersistentAgent,
    PersistentAgentCreditForecast,
    PersistentAgentEnabledTool,
)
from api.services.daily_credit_limits import get_agent_credit_multiplier
from api.services.embeddings import EmbeddingResult, generate_embeddings
from api.services.schedule_enforcement import cron_interval_seconds
from tasks.services import TaskCreditService
from util.constants.task_constants import TASKS_UNLIMITED

FORECAST_NEIGHBOR_LIMIT = 25
RECENCY_HALF_LIFE_DAYS = Decimal("90")
DECIMAL_ZERO = Decimal("0")
DISPLAY_QUANT = Decimal("1")


@dataclass(frozen=True)
class SimilarAgentSample:
    sample: HistoricalAgentCostSample
    distance: float | None


@dataclass(frozen=True)
class ForecastComputation:
    per_run_credits: Decimal | None
    daily_credits: Decimal | None
    monthly_credits: Decimal | None
    warning_level: str


def serialize_credit_forecast(forecast: PersistentAgentCreditForecast | None) -> dict[str, Any] | None:
    if forecast is None:
        return None
    return {
        "perRunCredits": _decimal_to_number(forecast.per_run_credits),
        "dailyCredits": _decimal_to_number(forecast.daily_credits),
        "monthlyCredits": _decimal_to_number(forecast.monthly_credits),
        "warningLevel": forecast.warning_level,
        "estimatedAt": forecast.estimated_at.isoformat() if forecast.estimated_at else None,
    }


def serialize_agent_credit_forecast(agent: PersistentAgent) -> dict[str, Any] | None:
    try:
        forecast = agent.credit_forecast
    except PersistentAgentCreditForecast.DoesNotExist:
        return None
    return serialize_credit_forecast(forecast)


def persist_agent_credit_forecast(agent: PersistentAgent) -> PersistentAgentCreditForecast:
    computation = estimate_agent_credit_forecast(agent)
    forecast, _created = PersistentAgentCreditForecast.objects.update_or_create(
        agent=agent,
        defaults={
            "per_run_credits": computation.per_run_credits,
            "daily_credits": computation.daily_credits,
            "monthly_credits": computation.monthly_credits,
            "warning_level": computation.warning_level,
            "estimated_at": timezone.now(),
        },
    )
    return forecast


def estimate_agent_credit_forecast(agent: PersistentAgent) -> ForecastComputation:
    embedding_text = build_agent_forecast_text(agent)
    embedding = generate_embedding(embedding_text)
    if embedding is None:
        warning_level = _build_warning(agent, monthly=None)
        return ForecastComputation(
            per_run_credits=None,
            daily_credits=None,
            monthly_credits=None,
            warning_level=warning_level,
        )

    samples = find_similar_agent_samples(embedding.vector, embedding.dimension, limit=FORECAST_NEIGHBOR_LIMIT)
    if not samples:
        warning_level = _build_warning(agent, monthly=None)
        return ForecastComputation(
            per_run_credits=None,
            daily_credits=None,
            monthly_credits=None,
            warning_level=warning_level,
        )

    tier_multiplier = get_agent_credit_multiplier(agent)
    per_run = _display_credits(_weighted_percentile(samples, "normalized_first_run_credits", Decimal("0.80"), tier_multiplier))
    daily = _display_credits(_weighted_percentile(samples, "normalized_daily_credits", Decimal("0.80"), tier_multiplier))
    monthly = _display_credits(_weighted_percentile(samples, "normalized_monthly_credits", Decimal("0.80"), tier_multiplier))

    if _has_recurring_schedule(agent):
        runs_per_day = estimate_schedule_runs_per_day(agent.schedule)
        if runs_per_day is not None and per_run is not None:
            schedule_daily = _display_credits(per_run * runs_per_day)
            schedule_monthly = _display_credits(schedule_daily * Decimal("30"))
            daily = _max_decimal(daily, schedule_daily)
            monthly = _max_decimal(monthly, schedule_monthly)
    else:
        daily = DECIMAL_ZERO
        monthly = DECIMAL_ZERO

    warning_level = _build_warning(agent, monthly=monthly)
    return ForecastComputation(
        per_run_credits=per_run,
        daily_credits=daily,
        monthly_credits=monthly,
        warning_level=warning_level,
    )


def build_agent_forecast_text(agent: PersistentAgent) -> str:
    enabled_tools = list(
        PersistentAgentEnabledTool.objects.filter(agent=agent)
        .order_by("tool_full_name")
        .values_list("tool_full_name", flat=True)
    )
    owner_type = "organization" if agent.organization_id else "personal"
    tier_key = getattr(getattr(agent, "preferred_llm_tier", None), "key", "standard")
    channel_hints = _channel_hints_for_agent(agent)
    parts = [
        f"Agent name: {agent.name or ''}",
        f"Owner type: {owner_type}",
        f"Intelligence tier: {tier_key}",
        f"Schedule: {_schedule_summary(agent.schedule)}",
        f"Channels: {', '.join(channel_hints) if channel_hints else 'web'}",
        f"Enabled tools: {', '.join(enabled_tools) if enabled_tools else 'none'}",
        f"Planning plan: {agent.planning_plan or ''}",
        f"Charter: {agent.charter or ''}",
    ]
    return "\n".join(parts).strip()


def build_historical_sample_embedding_text(
    *,
    agent_name: str | None,
    owner_type: str | None,
    tier_key: str | None,
    schedule_summary: str | None,
    enabled_tools: Sequence[str] | None,
    planning_plan: str | None,
    charter_text: str | None,
) -> str:
    parts = [
        f"Agent name: {agent_name or ''}",
        f"Owner type: {owner_type or ''}",
        f"Intelligence tier: {tier_key or ''}",
        f"Schedule: {schedule_summary or 'none'}",
        f"Channels: unknown",
        f"Enabled tools: {', '.join(enabled_tools or []) if enabled_tools else 'none'}",
        f"Planning plan: {planning_plan or ''}",
        f"Charter: {charter_text or ''}",
    ]
    return "\n".join(parts).strip()


def set_historical_sample_embedding(
    sample: HistoricalAgentCostSample,
    embedding: EmbeddingResult,
    *,
    save: bool = True,
) -> None:
    sample.embedding_model = embedding.model
    sample.embedding_dimension = embedding.dimension
    sample.embedding_text_hash = hashlib.sha256((sample.embedding_text or "").encode("utf-8")).hexdigest()
    if save:
        sample.save(update_fields=["embedding_model", "embedding_dimension", "embedding_text_hash", "updated_at"])
    _write_sample_vector(sample.id, embedding.vector)


def generate_embedding(text: str) -> EmbeddingResult | None:
    return generate_embeddings([text])


def find_similar_agent_samples(
    vector: Sequence[float],
    dimension: int,
    *,
    limit: int,
) -> list[SimilarAgentSample]:
    if not vector or dimension <= 0:
        return []
    vector_literal = _pgvector_literal(vector)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, embedding_vector <=> %s::vector AS distance
            FROM api_historicalagentcostsample
            WHERE embedding_vector IS NOT NULL
              AND embedding_dimension = %s
            ORDER BY embedding_vector <=> %s::vector
            LIMIT %s
            """,
            [vector_literal, dimension, vector_literal, limit],
        )
        rows = cursor.fetchall()
    ids = [row[0] for row in rows]
    if not ids:
        return []
    samples_by_id = HistoricalAgentCostSample.objects.in_bulk(ids)
    return [
        SimilarAgentSample(sample=samples_by_id[row[0]], distance=float(row[1]) if row[1] is not None else None)
        for row in rows
        if row[0] in samples_by_id
    ]


def estimate_schedule_runs_per_day(schedule_value: str | None) -> Decimal | None:
    if not schedule_value:
        return None
    try:
        schedule_obj = ScheduleParser.parse(schedule_value)
    except ValueError:
        return None
    interval_seconds: float | None = None
    if isinstance(schedule_obj, celery_schedule):
        interval_seconds = float(schedule_obj.run_every.total_seconds())
    elif isinstance(schedule_obj, crontab):
        interval_seconds = float(cron_interval_seconds(schedule_obj))
    if interval_seconds is None or interval_seconds <= 0:
        return None
    return Decimal("86400") / Decimal(str(interval_seconds))


def _write_sample_vector(sample_id: Any, vector: Sequence[float]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            'UPDATE "api_historicalagentcostsample" SET "embedding_vector" = %s::vector WHERE "id" = %s',
            [_pgvector_literal(vector), sample_id],
        )


def _pgvector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".12g") for value in vector) + "]"


def _weighted_percentile(
    samples: Sequence[SimilarAgentSample],
    field_name: str,
    percentile: Decimal,
    tier_multiplier: Decimal,
) -> Decimal | None:
    weighted_values: list[tuple[Decimal, Decimal]] = []
    now = timezone.now()
    for item in samples:
        raw_value = getattr(item.sample, field_name)
        value = _coerce_decimal(raw_value)
        if value is None:
            continue
        similarity_weight = _similarity_weight(item.distance)
        recency_weight = _recency_weight(getattr(item.sample, "last_observed_at_source", None), now)
        weight = similarity_weight * recency_weight
        weighted_values.append((value * tier_multiplier, weight))

    if not weighted_values:
        return None

    weighted_values.sort(key=lambda pair: pair[0])
    total_weight = sum(weight for _value, weight in weighted_values)
    threshold = total_weight * percentile
    running = DECIMAL_ZERO
    for value, weight in weighted_values:
        running += weight
        if running >= threshold:
            return value
    return weighted_values[-1][0]


def _similarity_weight(distance: float | None) -> Decimal:
    if distance is None or math.isnan(distance):
        return Decimal("0.25")
    similarity = max(0.01, 1.0 - max(0.0, float(distance)))
    return Decimal(str(similarity))


def _recency_weight(observed_at: Any, now: Any) -> Decimal:
    if observed_at is None:
        return Decimal("0.5")
    age_days = Decimal(str(max((now - observed_at).total_seconds(), 0))) / Decimal("86400")
    return Decimal("1") / (Decimal("1") + (age_days / RECENCY_HALF_LIFE_DAYS))


def _build_warning(
    agent: PersistentAgent,
    *,
    monthly: Decimal | None,
) -> str:
    owner = agent.organization or agent.user
    available = TaskCreditService.calculate_available_tasks_for_owner(owner)
    if available == Decimal(TASKS_UNLIMITED):
        return PersistentAgentCreditForecast.WarningLevel.NONE

    available = _coerce_decimal(available) or DECIMAL_ZERO
    if available <= DECIMAL_ZERO:
        return PersistentAgentCreditForecast.WarningLevel.HIGH

    if monthly is not None and monthly > available:
        return PersistentAgentCreditForecast.WarningLevel.HIGH
    if monthly is not None and monthly >= (available * Decimal("0.5")):
        return PersistentAgentCreditForecast.WarningLevel.MEDIUM
    return PersistentAgentCreditForecast.WarningLevel.NONE


def _channel_hints_for_agent(agent: PersistentAgent) -> list[str]:
    if not getattr(agent, "id", None):
        return []
    try:
        return sorted(set(agent.comms_endpoints.values_list("channel", flat=True)))
    except AttributeError:
        return []


def _schedule_summary(schedule_value: str | None) -> str:
    if not schedule_value:
        return "none"
    runs_per_day = estimate_schedule_runs_per_day(schedule_value)
    if runs_per_day is None:
        return schedule_value
    if runs_per_day >= Decimal("1"):
        return f"{schedule_value} ({runs_per_day.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} runs/day)"
    days_per_run = (Decimal("1") / runs_per_day).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{schedule_value} (every {days_per_run} days)"


def _has_recurring_schedule(agent: PersistentAgent) -> bool:
    return bool((agent.schedule or "").strip())


def _display_credits(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if value < DECIMAL_ZERO:
        value = DECIMAL_ZERO
    return value.quantize(DISPLAY_QUANT, rounding=ROUND_HALF_UP)


def _decimal_to_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _coerce_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _max_decimal(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
