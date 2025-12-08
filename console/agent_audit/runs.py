from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from typing import Iterable, List, Optional

from django.db.models import Q
from django.utils import timezone

from api.models import (
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)
from console.agent_audit.serializers import serialize_completion, serialize_message, serialize_prompt_meta, serialize_tool_call


def _normalize_ts(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    dt = dt.astimezone(dt_timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


@dataclass
class RunBoundary:
    """Time window for a single PROCESS_EVENTS loop."""

    run_id: str
    started_at: datetime
    ended_at: Optional[datetime]
    sequence: int

    @property
    def started_at_iso(self) -> str:
        return _dt_to_iso(self.started_at) or ""

    @property
    def ended_at_iso(self) -> str | None:
        return _dt_to_iso(self.ended_at)


def _process_events_qs(agent: PersistentAgent):
    return (
        PersistentAgentSystemStep.objects.filter(
            step__agent=agent,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            step__description="Process events",
        )
        .select_related("step")
        .order_by("-step__created_at", "-step__id")
    )


def _paginate_boundaries(agent: PersistentAgent, cursor_dt: datetime | None, cursor_id: str | None, limit: int) -> tuple[list[RunBoundary], bool]:
    qs = _process_events_qs(agent)
    if cursor_dt:
        qs = qs.filter(
            Q(step__created_at__lt=cursor_dt)
            | Q(step__created_at=cursor_dt, step__id__lt=cursor_id)
        )
    steps = list(qs[: limit + 1])
    has_more = len(steps) > limit
    if has_more:
        steps = steps[:limit]

    boundaries: list[RunBoundary] = []
    for idx, sys_step in enumerate(steps):
        sequence = (
            PersistentAgentSystemStep.objects.filter(
                step__agent=agent,
                code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                step__created_at__lte=sys_step.step.created_at,
            ).count()
        )
        boundaries.append(
            RunBoundary(
                run_id=str(sys_step.step_id),
                started_at=_normalize_ts(sys_step.step.created_at),
                ended_at=None,
                sequence=sequence,
            )
        )
    return boundaries, has_more


def _attach_end_times(boundaries: list[RunBoundary]) -> None:
    sorted_bounds = sorted(boundaries, key=lambda b: b.started_at, reverse=True)
    for i, boundary in enumerate(sorted_bounds):
        next_boundary = sorted_bounds[i + 1] if i + 1 < len(sorted_bounds) else None
        boundary.ended_at = _normalize_ts(next_boundary.started_at) if next_boundary else None


def fetch_run_boundaries(
    agent: PersistentAgent,
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[RunBoundary], bool]:
    cursor_dt: datetime | None = None
    cursor_id: str | None = None
    if cursor:
        try:
            ts_str, step_id = cursor.split(":", 1)
            cursor_dt = datetime.fromisoformat(ts_str)
            if timezone.is_naive(cursor_dt):
                cursor_dt = timezone.make_aware(cursor_dt, timezone.get_current_timezone())
            cursor_id = step_id
        except Exception:
            cursor_dt = None
            cursor_id = None

    boundaries, has_more = _paginate_boundaries(agent, cursor_dt, cursor_id, limit)
    _attach_end_times(boundaries)
    return boundaries, has_more


def _serialize_token_totals(completions: Iterable[PersistentAgentCompletion]) -> dict:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}
    for completion in completions:
        totals["prompt_tokens"] += completion.prompt_tokens or 0
        totals["completion_tokens"] += completion.completion_tokens or 0
        totals["total_tokens"] += completion.total_tokens or 0
        totals["cached_tokens"] += completion.cached_tokens or 0
    return totals


def build_run_payload(
    agent: PersistentAgent,
    boundary: RunBoundary,
) -> dict:
    start = boundary.started_at
    end = boundary.ended_at
    completion_qs = (
        PersistentAgentCompletion.objects.filter(agent=agent, created_at__gte=start)
        .order_by("created_at", "id")
    )
    if end:
        completion_qs = completion_qs.filter(created_at__lt=end)
    completions = list(
        completion_qs.prefetch_related("steps__tool_call", "steps__llm_prompt_archive")
    )

    step_qs = (
        PersistentAgentStep.objects.filter(agent=agent, created_at__gte=start)
        .select_related("tool_call", "completion")
        .prefetch_related("llm_prompt_archive")
        .order_by("created_at", "id")
    )
    if end:
        step_qs = step_qs.filter(created_at__lt=end)
    steps = list(step_qs)

    message_qs = (
        PersistentAgentMessage.objects.filter(owner_agent=agent, timestamp__gte=start)
        .select_related("from_endpoint", "to_endpoint", "conversation", "peer_agent", "owner_agent")
        .prefetch_related("attachments")
        .order_by("timestamp", "seq")
    )
    if end:
        message_qs = message_qs.filter(timestamp__lt=end)
    messages = list(message_qs)

    events: List[dict] = []

    step_prompt_lookup = {}
    for step in steps:
        if getattr(step, "llm_prompt_archive", None):
            step_prompt_lookup[step.id] = serialize_prompt_meta(step.llm_prompt_archive)

    for completion in completions:
        prompt_data = None
        related_steps = [s for s in steps if s.completion_id == completion.id]
        for candidate in related_steps:
            prompt_data = serialize_prompt_meta(getattr(candidate, "llm_prompt_archive", None))
            if prompt_data:
                break

        tool_calls = []
        for step in related_steps:
            if not getattr(step, "tool_call", None):
                continue
            tool_calls.append(serialize_tool_call(step))

        prompt_archive_obj = getattr(related_steps[0], "llm_prompt_archive", None) if related_steps else None
        completion_event = serialize_completion(
            completion,
            prompt_archive=prompt_archive_obj if not prompt_data else None,
            tool_calls=tool_calls,
        )
        if prompt_data:
            completion_event["prompt_archive"] = prompt_data
        events.append(completion_event)

    for message in messages:
        events.append(serialize_message(message))

    events.sort(key=lambda e: (e.get("timestamp") or "", e.get("id", "")))

    return {
        "run_id": boundary.run_id,
        "sequence": boundary.sequence,
        "started_at": boundary.started_at_iso,
        "ended_at": boundary.ended_at_iso,
        "events": events,
        "token_totals": _serialize_token_totals(completions),
    }


def resolve_run_id_for_timestamp(agent_id: str, timestamp: datetime | None) -> str | None:
    """Return the run (PROCESS_EVENTS step id) active at the provided timestamp."""

    if timestamp is None:
        return None
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    run_id = (
        PersistentAgentSystemStep.objects.filter(
            step__agent_id=agent_id,
            code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
            step__created_at__lte=timestamp,
        )
        .order_by("-step__created_at", "-step__id")
        .values_list("step_id", flat=True)
        .first()
    )
    return str(run_id) if run_id else None
