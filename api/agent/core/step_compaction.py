from __future__ import annotations

"""On-demand *step* history compaction for persistent agents.

This mirrors :pymod:`api.agent.core.compaction` (message compaction) but works on
:class:`~api.models.PersistentAgentStep` records.  The algorithm is identical:

1. Hold a short DB lock to decide if compaction is needed.
2. If raw steps > ``RAW_STEP_LIMIT`` fetch them *outside* the lock and generate a
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
from django.utils import timezone
from django.db import transaction
from django.db.models import Case, F, TextField, Value, When
from django.db.models.functions import Concat, Length, Substr, Greatest

from ...models import (
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentStepSnapshot,
    PersistentAgentToolCall,
    PersistentAgentCronTrigger,
    PersistentAgentSystemStep,
)

import logging
from opentelemetry import trace

from .llm_config import get_summarization_llm_config
from .llm_utils import run_completion

__all__ = [
    "ensure_steps_compacted",
    "RAW_STEP_LIMIT",
    "llm_summarise_steps",
]

# --------------------------------------------------------------------------- #
#  Tunables                                                                   #
# --------------------------------------------------------------------------- #

RAW_STEP_LIMIT: int = getattr(settings, "PA_RAW_STEP_LIMIT", 100)
"""Number of raw steps allowed after the last snapshot before triggering
compaction.  Override with ``PA_RAW_STEP_LIMIT`` in Django settings for
experimentation."""

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

    def to_summary_str(self) -> str:
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
    summarise_fn: Callable[[str, Sequence[StepData], str], str] | None = None,
    safety_identifier: str | None = None,
) -> None:
    """Ensure the agent's *step* history is compacted up-to-date.

    Logic mirrors :func:`api.agent.core.compaction.ensure_comms_compacted` but
    operates on :class:`~api.models.PersistentAgentStep` and produces
    :class:`~api.models.PersistentAgentStepSnapshot` records.
    """

    if summarise_fn is None:
        summarise_fn = _default_summarise  # type: ignore[assignment]

    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))

    # Determine the current limit dynamically so that test overrides using
    # `@override_settings(PA_RAW_STEP_LIMIT=...)` take effect even though
    # the module-level constant is evaluated at import time.
    raw_limit: int = getattr(settings, "PA_RAW_STEP_LIMIT", RAW_STEP_LIMIT)

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

        raw_count = (
            PersistentAgentStep.objects
            .filter(agent=agent_locked, created_at__gt=lower_bound)
            .count()
        )

        span.set_attribute("compaction.raw_steps", raw_count)
        span.set_attribute("compaction.raw_limit", raw_limit)

        if raw_count <= raw_limit:
            return  # Nothing to summarise.

        # Determine *upper* bound (created_at of last raw step) now so we can
        # detect races later.
        last_step_ts = (
            PersistentAgentStep.objects
            .filter(agent=agent_locked, created_at__gt=lower_bound)
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        assert last_step_ts is not None  # raw_count > 0 so cannot be None

        previous_summary = last_snap.summary if last_snap else ""

    # ------------------------------ Phase 2 ------------------------------ #
    # Slow work: fetch & summarise *outside* the lock.
    raw_steps_struct = _fetch_and_structurise_steps(agent, lower_bound, last_step_ts)

    try:
        with tracer.start_as_current_span("COMPACT Step Summarise") as summarise_span:
            summarise_span.set_attribute("steps.count", len(raw_steps_struct))
            new_summary = summarise_fn(previous_summary, raw_steps_struct, safety_identifier)
    except Exception:  # pragma: no cover – downstream can retry
        logger.exception("step summarise_fn failed; skipping compaction for agent %s", agent.id)
        return

    # ------------------------------ Phase 3 ------------------------------ #
    # Persist snapshot under lock if no-one beat us.
    with transaction.atomic():
        agent_locked = PersistentAgent.objects.select_for_update().get(id=agent.id)

        race = (
            PersistentAgentStepSnapshot.objects
            .filter(agent=agent_locked, snapshot_until__gte=last_step_ts)
            .exists()
        )
        if race:
            span.set_attribute("compaction.skipped", True)
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
            snapshot_until=last_step_ts,
            summary=new_summary,
        )

        span.set_attribute("compaction.snapshot_until", last_step_ts.isoformat())
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
        .select_related("tool_call", "cron_trigger", "system_step")
        # Defer the potentially huge text blob – we'll bulk-fetch it later.
        .defer("tool_call__result")
        .order_by("created_at")
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
#  Default summariser (placeholder)                                           #
# --------------------------------------------------------------------------- #

def _default_summarise(previous: str, steps: Sequence[StepData], safety_identifier: str | None = None) -> str:  # noqa: D401 Simple verb
    """Fallback summariser for testing and error cases.

    Groups recent steps by type and appends bullet-point lines under an
    "--- Recent Steps ---" header.  Used as a fallback when LLM summarisation
    fails and for deterministic behavior in tests.
    """

    # Split by type for deterministic output useful in tests.
    recent_lines: List[str] = ["--- Recent Steps (%d) ---" % len(steps)]
    for s in steps:
        recent_lines.append("• " + s.to_summary_str())

    joined = "\n".join(recent_lines)
    joined = joined + "\n" + ("Safety ID: " + str(safety_identifier) if safety_identifier else "")
    return previous + ("\n" if previous else "") + joined 


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
) -> str:
    """Summarise *previous* + *steps* via LiteLLM.

    This is the primary summarisation function used in production.  Unit-tests
    can inject the deterministic placeholder instead.  Failure gracefully falls back.

    Args:
        previous: Previous summary text to extend.
        steps: New steps to incorporate.
        safety_identifier: Optional safety identifier for API calls.
        agent: Optional agent instance for config lookup.
        routing_profile: Optional LLMRoutingProfile for eval routing.
    """

    # Convert structured dataclasses to concise text lines.
    step_lines: list[str] = [s.to_summary_str() for s in steps]
    recent_block = "\n".join(step_lines)

    prompt = [
        {
            "role": "system",
            "content": (
                "You are an assistant that maintains a running high-level summary of "
                "an AI agent's execution steps. Given the *existing* summary and a "
                "list of new raw steps, produce an updated concise summary."
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
        model, params = get_summarization_llm_config(agent=agent, routing_profile=routing_profile)

        if model.startswith("openai"):
            if safety_identifier:
                params["safety_identifier"] = str(safety_identifier)

        resp = run_completion(model=model, messages=prompt, params=params)
        return resp.choices[0].message.content.strip()
    except Exception:
        logger.exception("LiteLLM step summarisation failed – falling back to fallback summariser")
        return _default_summarise(previous, steps, safety_identifier)
