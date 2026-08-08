"""On-demand compaction of persistent-agent communication history.

Raw messages remain durable; summaries are created only while building prompts.
"""
from __future__ import annotations

from typing import Callable, List, Sequence, Optional

from django.conf import settings
from django.db import transaction

from ...models import PersistentAgent, PersistentAgentMessage, PersistentAgentCommsSnapshot, PersistentAgentCompletion

import logging

from opentelemetry import trace

from .llm_config import get_summarization_llm_config
from .llm_utils import run_completion
from .token_usage import log_agent_completion, set_usage_span_attributes
from ..structured_peer_payload import (
    StructuredPeerPayload,
    canonicalize_structured_peer_payload,
    get_structured_peer_payload,
)
from ..comms.source_metadata import get_message_source_metadata

RAW_MSG_LIMIT: int = getattr(settings, "PA_RAW_MSG_LIMIT", 20)
COMMS_COMPACTION_TAIL: int = max(0, getattr(settings, "PA_COMMS_COMPACTION_TAIL", 5))
COMMS_COMPACTION_COMPONENT_CHAR_LIMIT = 4000

tracer = trace.get_tracer("gobii.utils")
logger = logging.getLogger(__name__)

__all__ = [
    "ensure_comms_compacted",
    "RAW_MSG_LIMIT",
    "COMMS_COMPACTION_TAIL",
    "ensure_steps_compacted",
    "llm_summarise_comms",
]

@tracer.start_as_current_span("COMPACT Comms History")
def ensure_comms_compacted(
    *,
    agent: PersistentAgent,
    summarise_fn: Callable[[str, Sequence[PersistentAgentMessage], str], str] | None = None,
    safety_identifier: str | None = None,
) -> None:
    """Summarize old messages when the uncompacted history exceeds its limit."""
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
            .select_related("from_endpoint", "to_endpoint", "conversation", "peer_agent")
            .order_by("timestamp")
        )

        # Materialise once; len(raw_messages) avoids an extra COUNT(*) query.
        raw_messages: List[PersistentAgentMessage] = list(raw_qs)

        raw_count = len(raw_messages)
        span.set_attribute("compaction.raw_messages", raw_count)
        span.set_attribute("compaction.raw_limit", RAW_MSG_LIMIT)

        if raw_count <= RAW_MSG_LIMIT:
            return  # Nothing to summarise yet.

        # Keep the most recent messages raw; compact everything earlier.
        tail_count = min(COMMS_COMPACTION_TAIL, max(raw_count - 1, 0))
        compacted_count = max(raw_count - tail_count, 0)
        messages_to_compact = raw_messages[:compacted_count]
        if not messages_to_compact:
            return

        previous_summary = last_snap.summary if last_snap else ""

        # Provide the value we will later use to detect race conditions.
        snapshot_until = messages_to_compact[-1].timestamp

    # ------------------------------ Phase 2 ------------------------------ #
    # Slow work happens *outside* the lock.
    try:
        with tracer.start_as_current_span("COMPACT Summarise") as summarise_span:
            summarise_span.set_attribute("messages.count", len(raw_messages))
            new_summary = summarise_fn(previous_summary, messages_to_compact, safety_identifier)
    except Exception:  # pragma: no cover – downstream will handle retry logic
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


def _format_structured_payload_for_compaction(payload: StructuredPeerPayload) -> str:
    serialized = canonicalize_structured_peer_payload(payload)
    if len(serialized) <= COMMS_COMPACTION_COMPONENT_CHAR_LIMIT:
        return serialized

    preview = {
        "_compaction_truncated": True,
        "_original_char_count": len(serialized),
        "_json_prefix": "",
    }
    low, high = 0, COMMS_COMPACTION_COMPONENT_CHAR_LIMIT
    while low < high:
        midpoint = (low + high + 1) // 2
        preview["_json_prefix"] = serialized[:midpoint]
        if (
            len(canonicalize_structured_peer_payload(preview))
            <= COMMS_COMPACTION_COMPONENT_CHAR_LIMIT
        ):
            low = midpoint
        else:
            high = midpoint - 1
    preview["_json_prefix"] = serialized[:low]
    return canonicalize_structured_peer_payload(preview)


def _format_message_party_for_compaction(message: PersistentAgentMessage) -> str:
    conversation = getattr(message, "conversation", None)
    peer_agent = getattr(message, "peer_agent", None)
    is_peer_dm = bool(conversation and getattr(conversation, "is_peer_dm", False))

    if message.is_outbound:
        endpoint = getattr(message, "to_endpoint", None)
        channel = getattr(endpoint, "channel", None) or getattr(conversation, "channel", None) or "message"
        recipient = (
            getattr(peer_agent, "name", None)
            if is_peer_dm
            else getattr(endpoint, "address", None) or getattr(conversation, "address", None)
        )
        return f"Outbound {channel} to {recipient or 'unknown recipient'}"

    endpoint = getattr(message, "from_endpoint", None)
    source_kind, source_label = get_message_source_metadata(getattr(message, "raw_payload", None))
    channel = (
        "peer DM"
        if is_peer_dm
        else source_kind or getattr(endpoint, "channel", None) or getattr(conversation, "channel", None) or "message"
    )
    sender = (
        getattr(peer_agent, "name", None)
        if is_peer_dm
        else source_label or getattr(endpoint, "address", None)
    )
    return f"Inbound {channel} from {sender or 'unknown sender'}"


def llm_summarise_comms(
    previous: str,
    messages: Sequence[PersistentAgentMessage],
    safety_identifier: str | None = None,
    *,
    agent: Optional[PersistentAgent] = None,
    routing_profile=None,
) -> str:
    """Summarise *previous* + *messages* via an LLM (LiteLLM).

    This is the primary summarisation function used in production. Unit-tests
    can inject alternative functions for deterministic behavior. If the LLM call
    fails we transparently fall back to the placeholder summariser so that the
    compaction pipeline is still resilient.

    Args:
        previous: Previous summary text to extend.
        messages: New messages to incorporate.
        safety_identifier: Optional safety identifier for API calls.
        agent: Optional agent instance for config lookup.
        routing_profile: Optional LLMRoutingProfile for eval routing.
    """

    # Speaker and transport identity must survive compaction. Generic User /
    # Assistant labels erase who said what in noisy multi-party histories.
    lines: list[str] = []
    for msg in messages:
        party = _format_message_party_for_compaction(msg)
        content_parts = [
            (msg.body or "").strip()[:COMMS_COMPACTION_COMPONENT_CHAR_LIMIT]
        ]
        payload = get_structured_peer_payload(msg.raw_payload)
        if payload is not None:
            payload_preview = _format_structured_payload_for_compaction(payload)
            content_parts.append(f"Structured payload:\n{payload_preview}")
        content = "\n".join(part for part in content_parts if part)
        lines.append(f"{party}: {content}")

    new_msgs_block = "\n".join(lines)

    prompt = [
        {
            "role": "system",
            "content": (
                "Maintain a compact current-state memory from an existing conversation summary and new messages. "
                "Preserve exact identifiers, owners, deadlines, evidence links, durable decisions, commitments, and "
                "unresolved work. Preserve still-operative scoped directives—including ownership changes, handoffs, "
                "stop/do-not-act instructions, permission boundaries, and commitments—with their actor, source, scope "
                "identifier, and effective constraint. A resolved event can have a continuing consequence: condense the "
                "event but retain that consequence until explicitly superseded, expired, or reassigned. Replace superseded "
                "state with the newest explicit correction; never retain replaced values as history. Keep competing claims "
                "only while unresolved. Drop resolved singletons only when they have no continuing consequence; collapse "
                "closed batches to counts. Delete repeated chatter/status and message "
                "mechanics unless they constrain current work. Default to under 2,000 characters; exceed that only when omitting unresolved facts would change a decision. Preserve who said, requested, observed, or changed each retained item and its source "
                "channel. Never transfer a statement to another person; keep uncertain attribution explicit."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Previous summary:\n{previous or '(none)'}\n\n"
                f"New messages:\n{new_msgs_block}\n\n"
                "Rewrite the state rather than appending a narrative. Return ONLY the updated concise summary text (no markdown, no code fences)."
            ),
        },
    ]

    try:
        provider, model, params = get_summarization_llm_config(agent=agent, routing_profile=routing_profile)

        if model.startswith("openai"):
            # GPT-4.1 is currently the only model supporting the `safety_identifier`
            # parameter, which is recommended by OpenAI for traceability.
            if safety_identifier:
                params["safety_identifier"] = str(safety_identifier)

        response = run_completion(model=model, messages=prompt, params=params)
        token_usage, usage = log_agent_completion(
            agent,
            completion_type=PersistentAgentCompletion.CompletionType.COMPACTION,
            response=response,
            model=model,
            provider=provider,
            pricing_model=params.get("pricing_model"),
            prompt_messages=prompt,
        )

        set_usage_span_attributes(trace.get_current_span(), usage)

        return response.choices[0].message.content.strip()
    except Exception:
        # Log and fall back to deterministic fallback so callers are not
        # blocked by transient LLM/network issues.
        logger.exception("LiteLLM summarisation failed – falling back to fallback summariser")
        return _default_summarise(previous, messages)

# Re-export for convenience – avoids changing existing imports elsewhere
from .step_compaction import ensure_steps_compacted  # noqa: E402, isort:skip 
