from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
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


class Command(BaseCommand):
    help = "Seed pgvector-backed historical agent credit forecast samples from a read-only source database."

    def add_arguments(self, parser):
        parser.add_argument("--source-database-url", required=True)
        parser.add_argument("--limit", type=int, default=5000)
        parser.add_argument("--generate-embeddings", action="store_true")

    def handle(self, *args, **options):
        source_database_url = options["source_database_url"]
        limit = max(1, int(options["limit"]))
        generate_embeddings = bool(options["generate_embeddings"])

        rows = list(_fetch_source_rows(source_database_url, limit))
        upserted = 0
        embedded = 0
        samples = []
        with transaction.atomic():
            for row in rows:
                sample = _upsert_sample(row, source_database_url)
                samples.append(sample)
                upserted += 1

        if generate_embeddings:
            for sample in samples:
                embedding = generate_embedding(sample.embedding_text)
                if embedding is not None:
                    set_historical_sample_embedding(sample, embedding)
                    embedded += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {upserted} historical agent forecast samples"
                + (f" and generated {embedded} embeddings." if generate_embeddings else ".")
            )
        )


def _fetch_source_rows(source_database_url: str, limit: int):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise CommandError("psycopg is required to seed from a source database.") from exc

    try:
        with psycopg.connect(source_database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cursor:
                cursor.execute(SOURCE_QUERY, [limit])
                yield from cursor.fetchall()
    except psycopg.Error as exc:
        raise CommandError(f"Failed to read source database: {exc}") from exc


def _upsert_sample(row: dict, source_database_url: str) -> HistoricalAgentCostSample:
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
        "source_database": _source_database_label(source_database_url),
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


def _source_database_label(source_database_url: str) -> str:
    if "@" in source_database_url:
        source_database_url = source_database_url.split("@", 1)[1]
    return source_database_url[:128]
