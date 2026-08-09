"""On-demand *step* history compaction for persistent agents.

This mirrors :pymod:`api.agent.core.compaction` (message compaction) but works on
:class:`~api.models.PersistentAgentStep` records.  The algorithm is identical:

1. Hold a short DB lock to decide if compaction is needed.
2. If raw steps exceed the configured limit, fetch them *outside* the lock and generate a
   new summary (typically via an LLM).
3. Re-acquire the lock to materialise a
   :class:`~api.models.PersistentAgentStepSnapshot`, aborting if another process
   beat us.

Additional caveats for steps:
    • We must support multiple step *types* (tool calls, cron triggers, etc.) in
      a **type-safe** manner.
    • ``PersistentAgentToolCall.result`` may be arbitrarily large – we **defer**
      loading it in the initial query and, when serialising, trim to the last
      *N* lines (default 2000).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Sequence, Union, Dict, Any, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Case, F, TextField, Value, When
from django.db.models.functions import Concat, Length, Substr, Greatest

from ...models import PersistentAgent, PersistentAgentStep, PersistentAgentStepSnapshot, PersistentAgentToolCall, PersistentAgentCronTrigger, PersistentAgentSystemStep, PersistentAgentCompletion

import logging
from opentelemetry import trace

from . import internal_reasoning
from .compaction_exceptions import CompactionSummaryError
from .llm_config import get_summarization_llm_config
from .llm_utils import run_completion
from .token_usage import log_agent_completion, set_usage_span_attributes

MAX_TOOL_RESULT_CHARS: int = 200_000
"""Maximum number of *trailing* characters retained from ``tool_call.result`` when
serialising a :class:`~api.models.PersistentAgentToolCall`.  Earlier content is
discarded to cap memory usage."""

# Shared tracer namespace
tracer = trace.get_tracer("gobii.utils")

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Structured, type-safe view of each step                                    #
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class _StepBase:
    step_id: str
    created_at: datetime  # naive/aware per settings.USE_TZ
    description: str

    def to_summary_str(self) -> str:  # pragma: no cover – implemented in subclass
        raise NotImplementedError


@dataclass(slots=True)
class ToolCallStep(_StepBase):
    tool_name: str
    tool_params: Optional[Dict[str, Any]]
    result_tail: str  # Already truncated

    def to_summary_str(self) -> str:
        params_preview = (
            str(self.tool_params)[:120] + "…" if self.tool_params else "{}"
        )
        result_preview = (
            self.result_tail.replace("\n", " ⏎ ")[:250] + "…"
            if self.result_tail and len(self.result_tail) > 250
            else self.result_tail
        )
        return f"🔧 {self.tool_name}({params_preview}) → {result_preview}"


@dataclass(slots=True)
class CronTriggerStep(_StepBase):
    cron_expression: str
    schedule_key: str
    schedule_name: str
    schedule_instruction: str
    scheduled_for: datetime | None

    def to_summary_str(self) -> str:
        if self.schedule_key:
            timing = self.scheduled_for.isoformat() if self.scheduled_for else self.cron_expression
            label = self.schedule_name or self.schedule_key
            return f"⏰ Scheduled {label} [{self.schedule_key}] for {timing}: {self.schedule_instruction}"
        return f"⏰ Cron: {self.cron_expression}"


@dataclass(slots=True)
class SystemStep(_StepBase):
    code: str
    notes: str

    def to_summary_str(self) -> str:
        notes_preview = self.notes.replace("\n", " ⏎ ")[:120] + ("…" if len(self.notes) > 120 else "")
        return f"⚙️  System[{self.code}]: {notes_preview}"


@dataclass(slots=True)
class GenericStep(_StepBase):
    def to_summary_str(self) -> str:
        desc_preview = self.description.replace("\n", " ⏎ ")[:150] + (
            "…" if len(self.description) > 150 else ""
        )
        return f"📝 {desc_preview}"


StepData = Union[ToolCallStep, CronTriggerStep, SystemStep, GenericStep]


# --------------------------------------------------------------------------- #
#  Public helper                                                              #
# --------------------------------------------------------------------------- #

@tracer.start_as_current_span("COMPACT Step History")
def ensure_steps_compacted(
    *,
    agent: PersistentAgent,
    summarise_fn: Callable[[str, Sequence[StepData], str | None], str],
    safety_identifier: str | None = None,
) -> None:
    """Ensure the agent's *step* history is compacted up-to-date.

    Logic mirrors :func:`api.agent.core.compaction.ensure_comms_compacted` but
    operates on :class:`~api.models.PersistentAgentStep` and produces
    :class:`~api.models.PersistentAgentStepSnapshot` records.
    """

    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))

    # Determine the current limit dynamically so that test overrides using
    # `@override_settings(PA_RAW_STEP_LIMIT=...)` take effect even though
    # the module-level constant is evaluated at import time.
    raw_limit: int = settings.PA_RAW_STEP_LIMIT
    tail_limit: int = max(0, settings.PA_STEP_COMPACTION_TAIL)

    # ------------------------------ Phase 1 ------------------------------ #
    # Decide *if* compaction is needed under a short lock.
    with transaction.atomic():
        agent_locked: PersistentAgent = (
            PersistentAgent.objects.select_for_update().get(id=agent.id)
        )

        last_snap: PersistentAgentStepSnapshot | None = (
            PersistentAgentStepSnapshot.objects
            .filter(agent=agent_locked)
            .order_by("-snapshot_until")
            .first()
        )

        lower_bound = last_snap.snapshot_until if last_snap else agent_locked.created_at

        raw_qs = (
            PersistentAgentStep.objects
            .filter(agent=agent_locked, created_at__gt=lower_bound)
            .order_by("created_at", "id")
        )

        raw_count = raw_qs.count()

        span.set_attribute("compaction.raw_steps", raw_count)
        span.set_attribute("compaction.raw_limit", raw_limit)

        if raw_count <= raw_limit:
            return  # Nothing to summarise.

        # Keep the most recent steps raw; compact everything earlier.
        tail_count = min(tail_limit, max(raw_count - 1, 0))
        compacted_count = max(raw_count - tail_count, 0)
        if compacted_count <= 0:
            return

        snapshot_until = (
            raw_qs.values_list("created_at", flat=True)[compacted_count - 1]
        )

        # Queued and pending tool records are mutable. Keep them, and every
        # record sharing their timestamp, on the raw side of the cursor.
        earliest_mutable_tool_at = (
            raw_qs.filter(
                tool_call__status__in=(
                    PersistentAgentToolCall.Status.QUEUED,
                    PersistentAgentToolCall.Status.PENDING,
                )
            )
            .order_by("created_at", "id")
            .values_list("created_at", flat=True)
            .first()
        )
        if earliest_mutable_tool_at is not None and earliest_mutable_tool_at <= snapshot_until:
            stable_cutoff = (
                raw_qs.filter(created_at__lt=earliest_mutable_tool_at)
                .order_by("-created_at", "-id")
                .values_list("created_at", flat=True)
                .first()
            )
            if stable_cutoff is None:
                return
            snapshot_until = stable_cutoff

        previous_summary = last_snap.summary if last_snap else ""

        captured_step_ids = frozenset(
            raw_qs.filter(created_at__lte=snapshot_until).values_list("id", flat=True)
        )

    # ------------------------------ Phase 2 ------------------------------ #
    # Slow work: fetch & summarise *outside* the lock.
    raw_steps_struct = _fetch_and_structurise_steps(agent, lower_bound, snapshot_until)
    if not raw_steps_struct:
        if not previous_summary:
            return
        new_summary = previous_summary
    else:
        with tracer.start_as_current_span("COMPACT Step Summarise") as summarise_span:
            summarise_span.set_attribute("steps.count", len(raw_steps_struct))
            new_summary = summarise_fn(previous_summary, raw_steps_struct, safety_identifier)
        if not isinstance(new_summary, str) or not new_summary.strip():
            raise CompactionSummaryError(
                f"Step summarization returned an empty summary for agent {agent.id}"
            )

    # ------------------------------ Phase 3 ------------------------------ #
    # Persist snapshot under lock if no-one beat us.
    with transaction.atomic():
        agent_locked = PersistentAgent.objects.select_for_update().get(id=agent.id)

        race = (
            PersistentAgentStepSnapshot.objects
            .filter(agent=agent_locked, snapshot_until__gte=snapshot_until)
            .exists()
        )
        if race:
            span.set_attribute("compaction.skipped", True)
            return

        current_step_ids = frozenset(
            PersistentAgentStep.objects.filter(
                agent=agent_locked,
                created_at__gt=lower_bound,
                created_at__lte=snapshot_until,
            ).values_list("id", flat=True)
        )
        mutable_tool_exists = PersistentAgentToolCall.objects.filter(
            step__agent=agent_locked,
            step__created_at__gt=lower_bound,
            step__created_at__lte=snapshot_until,
            status__in=(
                PersistentAgentToolCall.Status.QUEUED,
                PersistentAgentToolCall.Status.PENDING,
            ),
        ).exists()
        if current_step_ids != captured_step_ids or mutable_tool_exists:
            span.set_attribute("compaction.skipped", True)
            span.set_attribute("compaction.skip_reason", "captured_range_changed")
            return

        prev_snap = (
            PersistentAgentStepSnapshot.objects
            .filter(agent=agent_locked)
            .order_by("-snapshot_until")
            .first()
        )

        PersistentAgentStepSnapshot.objects.create(
            agent=agent_locked,
            previous_snapshot=prev_snap,
            snapshot_until=snapshot_until,
            summary=new_summary,
        )

        span.set_attribute("compaction.snapshot_until", snapshot_until.isoformat())
        span.set_attribute("compaction.created", True)


# --------------------------------------------------------------------------- #
#  Internal helpers                                                           #
# --------------------------------------------------------------------------- #

def _fetch_and_structurise_steps(
    agent: PersistentAgent,
    lower_exclusive: datetime,
    upper_inclusive: datetime,
) -> List[StepData]:
    """Return structured ``StepData`` objects for *(lower, upper]`` timestamp.

    The query defers loading ``tool_call.result`` to avoid pulling potentially
    huge blobs into memory unless we later access them for serialisation.
    """

    qs = (
        PersistentAgentStep.objects
        .filter(
            agent=agent,
            created_at__gt=lower_exclusive,
            created_at__lte=upper_inclusive,
        )
        .exclude(description__startswith=internal_reasoning.INTERNAL_REASONING_PREFIX)
        .select_related("tool_call", "cron_trigger", "system_step")
        # Defer the potentially huge text blob – we'll bulk-fetch it later.
        .defer("tool_call__result")
        .order_by("created_at", "id")
    )

    steps: List[PersistentAgentStep] = list(qs)

    # ------------------------------------------------------------------ #
    #  Bulk-load tool_call.result to avoid N+1 SELECTs
    # ------------------------------------------------------------------ #
    tool_call_ids: list[str] = [
        s.tool_call.step_id  # PK reused from step_id
        for s in steps
        if getattr(s, "tool_call", None) is not None
    ]

    result_map: dict[str, str] = {}
    if tool_call_ids:
        # Instead of `values_list`, we use `values` and annotate a truncated
        # `result` field to avoid pulling huge text blobs into memory.
        result_qs = (
            PersistentAgentToolCall.objects
            .filter(step_id__in=tool_call_ids)
            .annotate(result_len=Length("result"))
            .annotate(
                result_tail=Case(
                    When(
                        result_len__gt=MAX_TOOL_RESULT_CHARS,
                        then=Concat(
                            Value("… (truncated) …\n"),
                            Substr(
                                "result",
                                Greatest(
                                    (F("result_len") - MAX_TOOL_RESULT_CHARS) + 1, 1
                                ),
                            ),
                        ),
                    ),
                    default=F("result"),
                    output_field=TextField(),
                )
            )
            .values("step_id", "result_tail")
        )
        result_map = {item["step_id"]: item["result_tail"] for item in result_qs}

    # ------------------------------------------------------------------ #
    #  Convert to structured dataclasses
    # ------------------------------------------------------------------ #
    out: List[StepData] = []
    for step in steps:
        out.append(_convert_step(step, result_map))
    return out


def _convert_step(step: PersistentAgentStep, result_map: dict[str, str]) -> StepData:  # noqa: C901 – complex but readable
    base_kwargs = {
        "step_id": str(step.id),
        "created_at": step.created_at,
        "description": step.description or "",
    }

    # Order of checks matters: a step can only have *one* satellite record.
    if hasattr(step, "tool_call") and step.tool_call is not None:
        tc: PersistentAgentToolCall = step.tool_call  # type: ignore[assignment]

        # Use pre-fetched and pre-truncated result from the database query.
        result_tail = result_map.get(step.id, "")

        return ToolCallStep(
            **base_kwargs,
            tool_name=tc.tool_name,
            tool_params=tc.tool_params,
            result_tail=result_tail,
        )

    if hasattr(step, "cron_trigger") and step.cron_trigger is not None:
        ct: PersistentAgentCronTrigger = step.cron_trigger  # type: ignore[assignment]
        return CronTriggerStep(
            **base_kwargs,
            cron_expression=ct.cron_expression,
            schedule_key=ct.schedule_key,
            schedule_name=ct.schedule_name,
            schedule_instruction=ct.schedule_instruction,
            scheduled_for=ct.scheduled_for,
        )

    if hasattr(step, "system_step") and step.system_step is not None:
        ss: PersistentAgentSystemStep = step.system_step  # type: ignore[assignment]
        return SystemStep(
            **base_kwargs,
            code=ss.code,
            notes=ss.notes or "",
        )

    # Fallback
    return GenericStep(**base_kwargs)


# --------------------------------------------------------------------------- #
#  Optional LiteLLM-powered summariser                                         
# --------------------------------------------------------------------------- #

def llm_summarise_steps(
    previous: str,
    steps: Sequence[StepData],
    safety_identifier: str | None = None,
    *,
    agent: Optional[PersistentAgent] = None,
    routing_profile=None,
    eval_run_id: str | None = None,
) -> str:
    """Summarise *previous* + *steps* via LiteLLM.

    This is the primary summarisation function used in production.  Unit-tests
    can inject the deterministic placeholder instead. Failures raise
    ``CompactionSummaryError`` so a worker can retry without advancing the snapshot.

    Args:
        previous: Previous summary text to extend.
        steps: New steps to incorporate.
        safety_identifier: Optional safety identifier for API calls.
        agent: Optional agent instance for config lookup.
        routing_profile: Optional LLMRoutingProfile for eval routing.
        eval_run_id: Optional eval run associated with completion accounting.
    """

    # Convert structured dataclasses to concise text lines.
    step_lines: list[str] = [s.to_summary_str() for s in steps]
    recent_block = "\n".join(step_lines)

    prompt = [
        {
            "role": "system",
            "content": (
                "Maintain a compact current-state summary of an agent's execution. Preserve durable outcomes, exact "
                "identifiers, created artifacts, source provenance, unresolved work, and active blockers. Preserve "
                "still-operative scoped directives—including ownership changes, handoffs, stop/do-not-act instructions, "
                "permission boundaries, and commitments—with their actor or source, scope identifier, and effective "
                "constraint. A resolved event can have a continuing consequence: condense the event but retain that "
                "consequence until explicitly superseded, expired, or reassigned. Replace superseded state; omit repeated "
                "attempts, resolved mechanics with no continuing consequence, and transient reasoning. Default to under "
                "2,000 characters; exceed that only when omitting unresolved facts would change a decision. Given the "
                "existing summary and new raw steps, rewrite the concise execution state rather than appending a log."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Previous summary:\n{previous or '(none)'}\n\n"
                f"New steps:\n{recent_block}\n\n"
                "Return ONLY the updated summary text (no markdown, no code fences)."
            ),
        },
    ]

    try:
        provider, model, params = get_summarization_llm_config(agent=agent, routing_profile=routing_profile)

        if model.startswith("openai"):
            if safety_identifier:
                params["safety_identifier"] = str(safety_identifier)

        resp = run_completion(
            model=model,
            messages=prompt,
            params=params,
        )
        token_usage, usage = log_agent_completion(
            agent,
            completion_type=PersistentAgentCompletion.CompletionType.STEP_COMPACTION,
            eval_run_id=eval_run_id,
            response=resp,
            model=model,
            provider=provider,
            pricing_model=params.get("pricing_model"),
            prompt_messages=prompt,
        )

        set_usage_span_attributes(trace.get_current_span(), usage)

        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.exception("LiteLLM step summarization failed")
        raise CompactionSummaryError("LiteLLM step summarization failed") from exc
