from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List

from django.db.models import Count, Min
from django.db.models.functions import TruncDay
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentStep,
)


@dataclass
class TimelineBucket:
    day: date
    count: int


@dataclass
class AuditTimeline:
    buckets: List[TimelineBucket]
    latest_day: date | None
    span_days: int


def _aggregate_counts(agent: PersistentAgent, *, start: datetime, end: datetime) -> Dict[date, int]:
    """Count audit-relevant events per day (inclusive of start, exclusive of end)."""

    def _bucket_counts(qs, dt_field: str) -> Dict[date, int]:
        rows = (
            qs.filter(**{f"{dt_field}__gte": start, f"{dt_field}__lt": end})
            .annotate(bucket=TruncDay(dt_field))
            .values("bucket")
            .annotate(count=Count("id"))
        )
        bucket_map: Dict[date, int] = {}
        for row in rows:
            bucket = row.get("bucket")
            if not bucket:
                continue
            bucket_local = timezone.localtime(bucket)
            bucket_date = bucket_local.date()
            bucket_map[bucket_date] = bucket_map.get(bucket_date, 0) + int(row.get("count") or 0)
        return bucket_map

    completion_counts = _bucket_counts(
        PersistentAgentCompletion.objects.filter(agent=agent),
        "created_at",
    )
    tool_call_counts = _bucket_counts(
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False),
        "created_at",
    )
    message_counts = _bucket_counts(
        PersistentAgentMessage.objects.filter(owner_agent=agent),
        "timestamp",
    )

    combined: Dict[date, int] = {}
    for bucket_map in (completion_counts, tool_call_counts, message_counts):
        for bucket, count in bucket_map.items():
            combined[bucket] = combined.get(bucket, 0) + count
    return combined


def _earliest_activity_date(agent: PersistentAgent) -> date | None:
    candidates: List[date] = []

    def _maybe_add(value):
        if value:
            candidates.append(timezone.localtime(value).date())

    completion_min = (
        PersistentAgentCompletion.objects.filter(agent=agent)
        .aggregate(value=Min("created_at"))
        .get("value")
    )
    tool_min = (
        PersistentAgentStep.objects.filter(agent=agent, tool_call__isnull=False)
        .aggregate(value=Min("created_at"))
        .get("value")
    )
    message_min = (
        PersistentAgentMessage.objects.filter(owner_agent=agent)
        .aggregate(value=Min("timestamp"))
        .get("value")
    )

    _maybe_add(completion_min)
    _maybe_add(tool_min)
    _maybe_add(message_min)
    _maybe_add(getattr(agent, "created_at", None))

    if not candidates:
        return None
    return min(candidates)


def _start_of_day(dt_date: date) -> datetime:
    naive = datetime.combine(dt_date, time.min)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def build_audit_timeline(agent: PersistentAgent, *, days: int | None = None) -> AuditTimeline:
    today = timezone.localdate()
    earliest_date = _earliest_activity_date(agent) or today

    if days is not None:
        days = max(1, min(days, 365))
        start_date = max(earliest_date, today - timedelta(days=days - 1))
    else:
        start_date = earliest_date

    end_date = today
    start = timezone.make_aware(datetime.combine(start_date, time.min), timezone.get_current_timezone())
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), timezone.get_current_timezone())

    bucket_counts = _aggregate_counts(agent, start=start, end=end)
    buckets: List[TimelineBucket] = []

    current = start_date
    last_seen: date | None = None
    while current <= end_date:
        count = bucket_counts.get(current, 0)
        if count > 0:
            last_seen = current
        buckets.append(TimelineBucket(day=current, count=count))
        current = current + timedelta(days=1)

    return AuditTimeline(buckets=buckets, latest_day=last_seen, span_days=len(buckets))
