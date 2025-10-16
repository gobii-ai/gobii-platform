"""
Helpers for preventing duplicate outbound agent messages.

This module centralises the logic used by individual communication tools
to detect recent duplicate sends before persisting a new message.
"""

import difflib
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional, Any
from uuid import UUID

from django.utils import timezone

from ...models import PersistentAgent, PersistentAgentMessage

CHECK_WINDOW_MINUTES = 10
DEFAULT_SIMILARITY_THRESHOLD = 0.97


@dataclass
class DuplicateDetectionResult:
    """Outcome of a duplicate detection check."""

    reason: str
    previous_message: PersistentAgentMessage
    similarity: Optional[float] = None

    def to_error_response(self) -> Dict[str, Any]:
        """Return a serializable payload explaining the duplicate rejection."""
        when = self.previous_message.timestamp
        if self.reason == "exact":
            detail = "matches"
        else:
            detail = "is highly similar to"
        message = (
            f"Message blocked: content {detail} a message sent within the last {CHECK_WINDOW_MINUTES} minutes "
            "and may be a duplicate. Please revise before sending again."
        )
        payload: Dict[str, Any] = {
            "status": "error",
            "message": message,
            "duplicate_detected": True,
            "duplicate_reason": self.reason,
            "duplicate_timestamp": when.isoformat(),
        }
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

    cutoff = timezone.now() - timedelta(minutes=CHECK_WINDOW_MINUTES)
    qs = PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
        timestamp__gte=cutoff,
        from_endpoint__channel=channel,
    )

    if conversation_id:
        qs = qs.filter(conversation_id=conversation_id)

    if to_address:
        qs = qs.filter(to_endpoint__address=to_address)

    current_body = (body or "").strip()

    for previous_message in qs.order_by("-timestamp").iterator():
        previous_body = (previous_message.body or "").strip()
        if not previous_body:
            continue

        if previous_body == current_body:
            return DuplicateDetectionResult(reason="exact", previous_message=previous_message)

        ratio = difflib.SequenceMatcher(None, previous_body, current_body, autojunk=True).ratio()
        if ratio >= similarity_threshold:
            return DuplicateDetectionResult(
                reason="similarity", previous_message=previous_message, similarity=ratio
            )

    return None
