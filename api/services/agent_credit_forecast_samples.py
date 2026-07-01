import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from api.models import HistoricalAgentCostSample
from api.services.agent_credit_forecasts import (
    build_historical_sample_embedding_text,
    generate_embedding,
    set_historical_sample_embedding,
)


SOURCE_QUERY = """
WITH enabled_tools AS (
    SELECT
        agent_id,
        array_agg(tool_full_name ORDER BY tool_full_name) AS tools
    FROM api_persistentagentenabledtool
    GROUP BY agent_id
),
charged_steps AS (
    SELECT
        agent_id,
        COUNT(*) AS charged_step_count,
        MIN(created_at) AS first_step_at,
        MAX(created_at) AS last_step_at,
        SUM(credits_cost) AS total_credits
    FROM api_persistentagentstep
    WHERE credits_cost IS NOT NULL
    GROUP BY agent_id
),
tool_calls AS (
    SELECT
        step.agent_id,
        COUNT(*) AS tool_call_count
    FROM api_persistentagenttoolcall tool_call
    JOIN api_persistentagentstep step ON step.id = tool_call.step_id
    GROUP BY step.agent_id
)
SELECT
    agent.id AS source_agent_id,
    agent.name AS agent_name,
    agent.charter AS charter_text,
    agent.planning_plan AS planning_plan,
    agent.schedule AS schedule,
    agent.organization_id IS NOT NULL AS org_owned,
    tier.key AS tier_key,
    tier.credit_multiplier AS tier_credit_multiplier,
    agent.created_at AS created_at_source,
    agent.planning_completed_at AS planning_completed_at_source,
    charged.last_step_at AS last_observed_at_source,
    COALESCE(enabled_tools.tools, ARRAY[]::varchar[]) AS enabled_tools,
    COALESCE(charged.charged_step_count, 0) AS charged_step_count,
    COALESCE(tool_calls.tool_call_count, 0) AS tool_call_count,
    COALESCE((
        SELECT SUM(step.credits_cost)
        FROM api_persistentagentstep step
        WHERE step.agent_id = agent.id
          AND step.credits_cost IS NOT NULL
          AND step.created_at >= COALESCE(agent.planning_completed_at, agent.created_at)
          AND step.created_at < COALESCE(agent.planning_completed_at, agent.created_at) + INTERVAL '1 day'
    ), 0) AS first_run_credits,
    COALESCE(charged.total_credits, 0) AS observed_total_credits,
    GREATEST(
        EXTRACT(EPOCH FROM (COALESCE(charged.last_step_at, agent.created_at) - agent.created_at)) / 86400.0,
        1.0
    ) AS observation_days
FROM api_persistentagent agent
LEFT JOIN api_intelligencetier tier ON tier.id = agent.preferred_llm_tier_id
LEFT JOIN enabled_tools ON enabled_tools.agent_id = agent.id
LEFT JOIN charged_steps charged ON charged.agent_id = agent.id
LEFT JOIN tool_calls ON tool_calls.agent_id = agent.id
WHERE COALESCE(charged.charged_step_count, 0) > 0
  AND COALESCE(agent.planning_plan, agent.charter, '') <> ''
ORDER BY COALESCE(charged.last_step_at, agent.created_at) DESC
LIMIT %s
"""


@dataclass(frozen=True)
class AgentCreditForecastSampleSeedResult:
    upserted: int
    embedded: int
    skipped_embeddings: int
    dry_run: bool = False


def seed_agent_credit_forecast_samples(
    *,
    limit: int = 5000,
    generate_embeddings: bool = False,
    skip_existing_embeddings: bool = False,
    dry_run: bool = False,
) -> AgentCreditForecastSampleSeedResult:
    rows = list(fetch_source_rows(max(1, int(limit))))
    if dry_run:
        return AgentCreditForecastSampleSeedResult(
            upserted=len(rows),
            embedded=0,
            skipped_embeddings=0,
            dry_run=True,
        )

    samples: list[HistoricalAgentCostSample] = []
    with transaction.atomic():
        for row in rows:
            samples.append(upsert_sample(row))

    embedded = 0
    skipped_embeddings = 0
    if generate_embeddings:
        for sample in samples:
            if skip_existing_embeddings and sample_has_current_embedding(sample):
                skipped_embeddings += 1
                continue
            embedding = generate_embedding(sample.embedding_text)
            if embedding is not None:
                set_historical_sample_embedding(sample, embedding)
                embedded += 1

    return AgentCreditForecastSampleSeedResult(
        upserted=len(samples),
        embedded=embedded,
        skipped_embeddings=skipped_embeddings,
    )


def fetch_source_rows(limit: int):
    with connection.cursor() as cursor:
        cursor.execute(SOURCE_QUERY, [limit])
        columns = [column[0] for column in cursor.description]
        for row in cursor.fetchall():
            yield dict(zip(columns, row))


def upsert_sample(row: dict) -> HistoricalAgentCostSample:
    multiplier = _positive_decimal(row.get("tier_credit_multiplier")) or Decimal("1.000")
    first_run = _decimal(row.get("first_run_credits")) or Decimal("0")
    observed_total = _decimal(row.get("observed_total_credits")) or Decimal("0")
    observation_days = _positive_decimal(row.get("observation_days")) or Decimal("1")
    daily = observed_total / observation_days
    monthly = daily * Decimal("30")
    enabled_tools = list(row.get("enabled_tools") or [])
    schedule_summary = row.get("schedule") or "none"
    owner_type = "organization" if row.get("org_owned") else "personal"
    tier_key = row.get("tier_key") or "standard"
    embedding_text = build_historical_sample_embedding_text(
        agent_name=row.get("agent_name"),
        owner_type=owner_type,
        tier_key=tier_key,
        schedule_summary=schedule_summary,
        enabled_tools=enabled_tools,
        planning_plan=row.get("planning_plan"),
        charter_text=row.get("charter_text"),
    )
    source_agent_id = row["source_agent_id"]
    source_sample_id = f"agent:{source_agent_id}"
    now = timezone.now()
    defaults = {
        "source_agent_id": source_agent_id,
        "source_database": _source_database_label(),
        "agent_name": row.get("agent_name") or "",
        "charter_text": row.get("charter_text") or "",
        "planning_plan": row.get("planning_plan") or "",
        "schedule_summary": schedule_summary,
        "enabled_tools": enabled_tools,
        "owner_type": owner_type,
        "tier_key": tier_key,
        "tier_credit_multiplier": multiplier,
        "normalized_first_run_credits": first_run / multiplier,
        "normalized_daily_credits": daily / multiplier,
        "normalized_monthly_credits": monthly / multiplier,
        "charged_step_count": int(row.get("charged_step_count") or 0),
        "tool_call_count": int(row.get("tool_call_count") or 0),
        "observation_days": observation_days,
        "embedding_text": embedding_text,
        "source_metadata": {
            "seeded_at": now.isoformat(),
            "observed_total_credits": str(observed_total),
        },
        "created_at_source": row.get("created_at_source"),
        "planning_completed_at_source": row.get("planning_completed_at_source"),
        "last_observed_at_source": row.get("last_observed_at_source"),
    }
    sample, _created = HistoricalAgentCostSample.objects.update_or_create(
        source_sample_id=source_sample_id,
        defaults=defaults,
    )
    return sample


def sample_has_current_embedding(sample: HistoricalAgentCostSample) -> bool:
    expected_hash = hashlib.sha256((sample.embedding_text or "").encode("utf-8")).hexdigest()
    if not sample.embedding_dimension or sample.embedding_text_hash != expected_hash:
        return False
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT embedding_vector IS NOT NULL
            FROM api_historicalagentcostsample
            WHERE id = %s
            """,
            [sample.id],
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _positive_decimal(value) -> Decimal | None:
    result = _decimal(value)
    if result is None or result <= Decimal("0"):
        return None
    return result


def _source_database_label() -> str:
    database_name = str(connection.settings_dict.get("NAME") or "").strip()
    if database_name:
        return database_name[:128]
    return settings.GOBII_RELEASE_ENV[:128]
