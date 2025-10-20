"""
Helpers for preventing duplicate outbound agent messages.

This module centralises the logic used by individual communication tools
to detect recent duplicate sends before persisting a new message.
"""

import difflib
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

from ...models import PersistentAgent, PersistentAgentMessage

DEFAULT_SIMILARITY_THRESHOLD = 0.97


@dataclass
class DuplicateDetectionResult:
    """Outcome of a duplicate detection check."""

    reason: str
    previous_message: PersistentAgentMessage
    similarity: Optional[float] = None

    def to_error_response(self) -> Dict[str, Any]:
        """Return a serializable payload explaining the duplicate rejection."""
        if self.reason == "exact":
            detail = "matches"
        else:
            detail = "is highly similar to"
        message = (
            f"Message blocked: content {detail} the previous message and may be a duplicate. "
            "Please revise before sending again."
        )
        payload: Dict[str, Any] = {
            "status": "error",
            "message": message,
            "duplicate_detected": True,
            "duplicate_reason": self.reason,
        }
        when = self.previous_message.timestamp
        if when:
            payload["duplicate_timestamp"] = when.isoformat()
        if self.similarity is not None:
            payload["duplicate_similarity"] = self.similarity
        return payload


def detect_recent_duplicate_message(
        agent: PersistentAgent,
        *,
        channel: str,
        body: str,
        to_address: Optional[str] = None,
        conversation_id: Optional[UUID] = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> Optional[DuplicateDetectionResult]:
    """
    Check whether the pending outbound message is a recent duplicate.

    The inspection operates in two passes:
    1. Exact match comparison on the full payload (subject + body where applicable).
    2. Fuzzy comparison using difflib.SequenceMatcher if no exact match is found.
    """
    if not body:
        return None

    qs = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
        from_endpoint__channel=channel,
    )

    if conversation_id:
        qs = qs.filter(conversation_id=conversation_id)

    if to_address:
        qs = qs.filter(to_endpoint__address=to_address)

    current_body = (body or "").strip()
    previous_message = qs.order_by("-timestamp").first()
    if not previous_message:
        return None

    previous_body = (previous_message.body or "").strip()
    if not previous_body:
        return None

    if previous_body == current_body:
        return DuplicateDetectionResult(reason="exact", previous_message=previous_message)

    ratio = difflib.SequenceMatcher(None, previous_body, current_body, autojunk=True).ratio()
    if ratio >= similarity_threshold:
        return DuplicateDetectionResult(
            reason="similarity", previous_message=previous_message, similarity=ratio
        )

    return None
