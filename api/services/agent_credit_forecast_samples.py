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


DEFAULT_BATCH_SIZE = 100


SOURCE_QUERY = """
WITH recent_agents AS (
    SELECT
        step.agent_id,
        MAX(step.created_at) AS last_observed_at_source
    FROM api_persistentagentstep step
    JOIN api_persistentagent agent ON agent.id = step.agent_id
    WHERE step.credits_cost IS NOT NULL
      AND COALESCE(agent.planning_plan, agent.charter, '') <> ''
    GROUP BY step.agent_id
    ORDER BY MAX(step.created_at) DESC
    LIMIT %s
),
enabled_tools AS (
    SELECT
        enabled.agent_id,
        array_agg(enabled.tool_full_name ORDER BY enabled.tool_full_name) AS tools
    FROM api_persistentagentenabledtool
    JOIN recent_agents recent ON recent.agent_id = enabled.agent_id
    GROUP BY enabled.agent_id
),
charged_steps AS (
    SELECT
        step.agent_id,
        COUNT(*) AS charged_step_count,
        SUM(step.credits_cost) AS total_credits
    FROM api_persistentagentstep step
    JOIN recent_agents recent ON recent.agent_id = step.agent_id
    WHERE step.credits_cost IS NOT NULL
    GROUP BY step.agent_id
),
tool_calls AS (
    SELECT
        step.agent_id,
        COUNT(*) AS tool_call_count
    FROM api_persistentagenttoolcall tool_call
    JOIN api_persistentagentstep step ON step.id = tool_call.step_id
    JOIN recent_agents recent ON recent.agent_id = step.agent_id
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
    recent.last_observed_at_source AS last_observed_at_source,
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
        EXTRACT(EPOCH FROM (COALESCE(recent.last_observed_at_source, agent.created_at) - agent.created_at)) / 86400.0,
        1.0
    ) AS observation_days
FROM recent_agents recent
JOIN api_persistentagent agent ON agent.id = recent.agent_id
LEFT JOIN api_intelligencetier tier ON tier.id = agent.preferred_llm_tier_id
LEFT JOIN enabled_tools ON enabled_tools.agent_id = agent.id
LEFT JOIN charged_steps charged ON charged.agent_id = agent.id
LEFT JOIN tool_calls ON tool_calls.agent_id = agent.id
ORDER BY recent.last_observed_at_source DESC
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
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> AgentCreditForecastSampleSeedResult:
    normalized_limit = max(1, int(limit))
    normalized_batch_size = max(1, int(batch_size))
    if dry_run:
        row_count = sum(1 for _row in fetch_source_rows(normalized_limit, batch_size=normalized_batch_size))
        return AgentCreditForecastSampleSeedResult(
            upserted=row_count,
            embedded=0,
            skipped_embeddings=0,
            dry_run=True,
        )

    upserted = 0
    embedded = 0
    skipped_embeddings = 0

    for rows in _batched(fetch_source_rows(normalized_limit, batch_size=normalized_batch_size), normalized_batch_size):
        with transaction.atomic():
            samples = [upsert_sample(row) for row in rows]

        upserted += len(samples)
        if not generate_embeddings:
            continue

        for sample in samples:
            if skip_existing_embeddings and sample_has_current_embedding(sample):
                skipped_embeddings += 1
                continue
            embedding = generate_embedding(sample.embedding_text)
            if embedding is not None:
                set_historical_sample_embedding(sample, embedding)
                embedded += 1

    return AgentCreditForecastSampleSeedResult(
        upserted=upserted,
        embedded=embedded,
        skipped_embeddings=skipped_embeddings,
    )


def fetch_source_rows(limit: int, *, batch_size: int = DEFAULT_BATCH_SIZE):
    with connection.chunked_cursor() as cursor:
        cursor.execute(SOURCE_QUERY, [limit])
        columns = [column[0] for column in cursor.description]
        while True:
            rows = cursor.fetchmany(max(1, int(batch_size)))
            if not rows:
                break
            for row in rows:
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


def _batched(rows, batch_size: int):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


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
