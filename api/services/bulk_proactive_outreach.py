import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from celery.exceptions import CeleryError
from django.utils import timezone
from kombu.exceptions import KombuError

from api.agent.tasks import process_agent_events_task
from api.models import PersistentAgent, PersistentAgentSystemStep
from api.services.proactive_activation import ProactiveActivationService


RECENT_PROACTIVE_LOOKBACK = timedelta(hours=6)


@dataclass
class BulkProactiveOutreachItem:
    agent_id: str
    status: str
    message: str
    agent_name: str = ""


@dataclass
class BulkProactiveOutreachResult:
    items: list[BulkProactiveOutreachItem]

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    @property
    def queued_count(self) -> int:
        return self.counts.get("queued", 0)


def trigger_bulk_proactive_outreach(
    raw_agent_ids: str,
    *,
    initiated_by: str,
    reason: str | None = None,
    dry_run: bool = False,
    skip_recent: bool = True,
    limit: int | None = None,
) -> BulkProactiveOutreachResult:
    parsed_items = _parse_agent_ids(raw_agent_ids, limit=limit)
    valid_ids = [item.agent_id for item in parsed_items if item.status == "pending"]
    agents_by_id = _load_agents(valid_ids)
    recent_agent_ids = _recent_proactive_agent_ids(valid_ids) if skip_recent and valid_ids else set()

    results: list[BulkProactiveOutreachItem] = []
    for parsed in parsed_items:
        if parsed.status != "pending":
            results.append(parsed)
            continue

        agent = agents_by_id.get(parsed.agent_id)
        if agent is None:
            results.append(
                BulkProactiveOutreachItem(
                    agent_id=parsed.agent_id,
                    status="missing",
                    message="Persistent agent not found.",
                )
            )
            continue

        if parsed.agent_id in recent_agent_ids:
            results.append(
                BulkProactiveOutreachItem(
                    agent_id=parsed.agent_id,
                    status="skipped_recent",
                    message="Skipped because this agent has a proactive trigger in the last 6 hours.",
                    agent_name=agent.name,
                )
            )
            continue

        try:
            ProactiveActivationService.validate_force_trigger_agent(agent)
        except ValueError as exc:
            results.append(
                BulkProactiveOutreachItem(
                    agent_id=parsed.agent_id,
                    status="blocked",
                    message=str(exc) or "Cannot trigger proactive outreach for this agent.",
                    agent_name=agent.name,
                )
            )
            continue

        if dry_run:
            results.append(
                BulkProactiveOutreachItem(
                    agent_id=parsed.agent_id,
                    status="dry_run",
                    message="Would record a proactive trigger and queue processing.",
                    agent_name=agent.name,
                )
            )
            continue

        try:
            ProactiveActivationService.force_trigger(
                agent,
                initiated_by=initiated_by,
                reason=reason,
            )
        except ValueError as exc:
            results.append(
                BulkProactiveOutreachItem(
                    agent_id=parsed.agent_id,
                    status="blocked",
                    message=str(exc) or "Cannot trigger proactive outreach for this agent.",
                    agent_name=agent.name,
                )
            )
            continue

        try:
            process_agent_events_task.delay(parsed.agent_id)
        except (CeleryError, KombuError) as exc:
            results.append(
                BulkProactiveOutreachItem(
                    agent_id=parsed.agent_id,
                    status="queue_failed",
                    message=f"Recorded proactive trigger, but failed to queue processing: {exc}",
                    agent_name=agent.name,
                )
            )
            continue

        results.append(
            BulkProactiveOutreachItem(
                agent_id=parsed.agent_id,
                status="queued",
                message="Recorded proactive trigger and queued processing.",
                agent_name=agent.name,
            )
        )

    return BulkProactiveOutreachResult(items=results)


def _parse_agent_ids(raw_agent_ids: str, *, limit: int | None = None) -> list[BulkProactiveOutreachItem]:
    tokens = [token.strip() for token in re.split(r"[\s,]+", raw_agent_ids or "") if token.strip()]
    if limit is not None:
        tokens = tokens[: max(limit, 0)]

    items: list[BulkProactiveOutreachItem] = []
    seen: set[str] = set()

    for token in tokens:
        try:
            agent_id = str(uuid.UUID(token))
        except (TypeError, ValueError):
            items.append(
                BulkProactiveOutreachItem(
                    agent_id=token,
                    status="invalid",
                    message="Invalid UUID.",
                )
            )
            continue

        if agent_id in seen:
            items.append(
                BulkProactiveOutreachItem(
                    agent_id=agent_id,
                    status="duplicate",
                    message="Duplicate UUID in input.",
                )
            )
            continue

        seen.add(agent_id)
        items.append(
            BulkProactiveOutreachItem(
                agent_id=agent_id,
                status="pending",
                message="Pending.",
            )
        )

    return items


def _load_agents(agent_ids: Sequence[str]) -> dict[str, PersistentAgent]:
    if not agent_ids:
        return {}

    agents = (
        PersistentAgent.objects.filter(id__in=agent_ids)
        .select_related(
            "user",
            "user__billing",
            "browser_use_agent",
            "organization",
            "organization__billing",
        )
    )
    return {str(agent.id): agent for agent in agents}


def _recent_proactive_agent_ids(agent_ids: Sequence[str]) -> set[str]:
    if not agent_ids:
        return set()

    lookback_start = timezone.now() - RECENT_PROACTIVE_LOOKBACK
    recent_ids = PersistentAgentSystemStep.objects.filter(
        step__agent_id__in=agent_ids,
        code=PersistentAgentSystemStep.Code.PROACTIVE_TRIGGER,
        step__created_at__gte=lookback_start,
    ).values_list("step__agent_id", flat=True)
    return {str(agent_id) for agent_id in recent_ids}
