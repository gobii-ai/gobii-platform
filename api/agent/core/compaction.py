"""Utilities for on-demand compaction / summarisation of persistent-agent
communication history.

The design follows the *single, pragmatic rule* documented in internal notes:
    • Only compact when building the LLM prompt (i.e. *on demand*).
    • Trigger compaction if the number of raw messages since the last snapshot
      exceeds `RAW_MSG_LIMIT`.

The system includes LLM-powered summarisation using LiteLLM with graceful 
fallbacks for resilience. Tests can provide a custom `summarise_fn` to bypass 
external network calls.
"""
from __future__ import annotations

from typing import Callable, List, Sequence

from django.conf import settings
from django.db import transaction

from ...models import (
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentCommsSnapshot,
)

import logging

from opentelemetry import trace

import litellm

from .llm_config import get_summarization_llm_config

# --------------------------------------------------------------------------- #
#  Tunables – can be overridden via Django settings for easy experimentation  #
# --------------------------------------------------------------------------- #
RAW_MSG_LIMIT: int = getattr(settings, "PA_RAW_MSG_LIMIT", 20)

# Tracer shared across backend codebase
tracer = trace.get_tracer("gobii.utils")

__all__ = ["ensure_comms_compacted", "RAW_MSG_LIMIT", "ensure_steps_compacted", "llm_summarise_comms"]

# --------------------------------------------------------------------------- #
#  Public helper                                                               
# --------------------------------------------------------------------------- #

@tracer.start_as_current_span("COMPACT Comms History")
def ensure_comms_compacted(
    *,
    agent: PersistentAgent,
    summarise_fn: Callable[[str, Sequence[PersistentAgentMessage], str], str] | None = None,
    safety_identifier: str | None = None,
) -> None:
    """Ensure the agent's communication history is compacted up to date.

    If the number of *raw* messages since the last
    :class:`~api.models.PersistentAgentCommsSnapshot` exceeds
    :data:`RAW_MSG_LIMIT`, we summarise that slice and write a new snapshot.

    Parameters
    ----------
    agent:
        The :class:`~api.models.PersistentAgent` whose message history we may
        compact.
    summarise_fn:
        Optional callable used to turn (previous_summary, new_messages) into a
        **new** summary string.  Defaults to a fallback implementation for
        testing and error resilience.

    safety_identifier:
        Optional safety identifier to help identify the caller in logs/traces.
        Recommended by OpenAI and others; only option for backwards compatibility
    """

    # Import inside function to avoid potential circular-import issues and to
    # keep compile-time of Django apps low.
    if summarise_fn is None:
        summarise_fn = _default_summarise  # type: ignore[assignment]

    # ------------------------------ Phase 1 ------------------------------ #
    # Decide *whether* we need to compact while holding the lock.  This keeps
    # the critical section extremely small – just a couple of quick queries –
    # and avoids blocking other writers whilst waiting on an LLM network call.
    span = trace.get_current_span()
    span.set_attribute("persistent_agent.id", str(agent.id))

    with transaction.atomic():
        agent_locked: PersistentAgent = (
            PersistentAgent.objects.select_for_update().get(id=agent.id)
        )

        last_snap: PersistentAgentCommsSnapshot | None = (
            PersistentAgentCommsSnapshot.objects
            .filter(agent=agent_locked)
            .order_by("-snapshot_until")
            .first()
        )

        lower_bound = (
            last_snap.snapshot_until if last_snap else agent_locked.created_at
        )

        raw_qs = (
            PersistentAgentMessage.objects
            .filter(owner_agent=agent_locked, timestamp__gt=lower_bound)
            .order_by("timestamp")
        )

        # Materialise once; len(raw_messages) avoids an extra COUNT(*) query.
        raw_messages: List[PersistentAgentMessage] = list(raw_qs)

        span.set_attribute("compaction.raw_messages", len(raw_messages))
        span.set_attribute("compaction.raw_limit", RAW_MSG_LIMIT)

        if len(raw_messages) <= RAW_MSG_LIMIT:
            return  # Nothing to summarise.

        previous_summary = last_snap.summary if last_snap else ""

        # Provide the value we will later use to detect race conditions.
        snapshot_until = raw_messages[-1].timestamp

    # ------------------------------ Phase 2 ------------------------------ #
    # Slow work happens *outside* the lock.
    try:
        with tracer.start_as_current_span("COMPACT Summarise") as summarise_span:
            summarise_span.set_attribute("messages.count", len(raw_messages))
            new_summary = summarise_fn(previous_summary, raw_messages, safety_identifier)
    except Exception:  # pragma: no cover – downstream will handle retry logic
        logger = logging.getLogger(__name__)
        logger.exception("summarise_fn failed; skipping compaction for agent %s", agent.id)
        return

    # ------------------------------ Phase 3 ------------------------------ #
    # Re-acquire the lock briefly to write the new snapshot iff no-one beat us.
    with transaction.atomic():
        agent_locked: PersistentAgent = (
            PersistentAgent.objects.select_for_update().get(id=agent.id)
        )

        # Abort if another process has already compacted the same or a further
        # range while we were waiting on the LLM.
        already_exists = (
            PersistentAgentCommsSnapshot.objects
            .filter(agent=agent_locked, snapshot_until__gte=snapshot_until)
            .exists()
        )
        if already_exists:
            span.set_attribute("compaction.skipped", True)
            return

        prev_snap: PersistentAgentCommsSnapshot | None = (
            PersistentAgentCommsSnapshot.objects
            .filter(agent=agent_locked)
            .order_by("-snapshot_until")
            .first()
        )

        PersistentAgentCommsSnapshot.objects.create(
            agent=agent_locked,
            previous_snapshot=prev_snap,
            snapshot_until=snapshot_until,
            summary=new_summary,
        )

        span.set_attribute("compaction.snapshot_until", snapshot_until.isoformat())
        span.set_attribute("compaction.created", True)

        # Again: do **not** delete raw messages; long-term pruning is out of
        # scope and can be handled by a background retention policy.


# --------------------------------------------------------------------------- #
#  Internal default summariser (placeholder)                                   
# --------------------------------------------------------------------------- #

def _default_summarise(
    previous: str,
    messages: Sequence[PersistentAgentMessage],
    safety_identifier: str | None = None,
) -> str:
    """Fallback summariser for testing and error cases.

    Provides deterministic output for unit tests and serves as a fallback when
    LLM summarisation fails. Simply concatenates the previous summary with a 
    placeholder line indicating the number of messages processed.
    """
    return (
        previous
        + ("\n" if previous else "")
        + f"[SUMMARY PLACEHOLDER for {len(messages)} messages]"
        + ("\n")
        + (f"[Called for {safety_identifier}]" if safety_identifier else "")
    )

# --------------------------------------------------------------------------- #
#  Optional LiteLLM-powered summariser                                          
# --------------------------------------------------------------------------- #

def llm_summarise_comms(
    previous: str,
    messages: Sequence[PersistentAgentMessage],
    safety_identifier: str | None = None,
) -> str:
    """Summarise *previous* + *messages* via an LLM (LiteLLM).

    This is the primary summarisation function used in production. Unit-tests
    can inject alternative functions for deterministic behavior. If the LLM call 
    fails we transparently fall back to the placeholder summariser so that the 
    compaction pipeline is still resilient.
    """

    # Build a compact textual representation of the new messages.  We include
    # sender role so the model can distinguish speaker turns.
    lines: list[str] = []
    for msg in messages:
        role = "Assistant" if msg.is_outbound else "User"
        # Truncate each message body to avoid huge token usage (4k chars cap).
        body_preview = msg.body[:4000]
        lines.append(f"{role}: {body_preview}")

    new_msgs_block = "\n".join(lines)

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a summarisation assistant. Given an *existing* summary\n"
                "of a conversation and a list of *new* messages, return an\n"
                "updated concise summary capturing the important details."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Previous summary:\n{previous or '(none)'}\n\n"
                f"New messages:\n{new_msgs_block}\n\n"
                "Return ONLY the updated summary text (no markdown, no code fences)."
            ),
        },
    ]

    try:
        model, params = get_summarization_llm_config()

        if model.startswith("openai"):
            # GPT-4.1 is currently the only model supporting the `safety_identifier`
            # parameter, which is recommended by OpenAI for traceability.
            if safety_identifier:
                params["safety_identifier"] = safety_identifier

        response = litellm.completion(model=model, messages=prompt, **params)
        return response.choices[0].message.content.strip()
    except Exception:
        # Log and fall back to deterministic fallback so callers are not
        # blocked by transient LLM/network issues.
        logger = logging.getLogger(__name__)
        logger.exception("LiteLLM summarisation failed – falling back to fallback summariser")
        return _default_summarise(previous, messages)

# Re-export for convenience – avoids changing existing imports elsewhere
from .step_compaction import ensure_steps_compacted  # noqa: E402, isort:skip 