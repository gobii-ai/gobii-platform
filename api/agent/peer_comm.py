"""Peer-to-peer agent messaging helpers."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from waffle import flag_is_active

from observability import traced

from api.models import (
    AgentCommPeerState,
    AgentPeerLink,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentMessage,
)

logger = logging.getLogger(__name__)


class PeerMessagingError(Exception):
    """Raised when peer messaging cannot proceed."""

    def __init__(self, message: str, *, status: str = "error", retry_at=None):
        super().__init__(message)
        self.status = status
        self.retry_at = retry_at


@dataclass
class PeerSendResult:
    """Outcome returned to tools after attempting a peer DM send."""

    status: str
    message: str
    remaining_credits: Optional[int]
    window_reset_at: Optional[datetime]
    retry_at: Optional[datetime] = None


class PeerMessagingService:
    """High-level helper coordinating peer-agent direct messages."""

    CHANNEL = CommsChannel.OTHER

    def __init__(self, agent: PersistentAgent, peer_agent: PersistentAgent):
        self.agent = agent
        self.peer_agent = peer_agent
        self.link = self._load_link(agent, peer_agent)

    @staticmethod
    def _load_link(agent: PersistentAgent, peer_agent: PersistentAgent) -> AgentPeerLink:
        pair_key = AgentPeerLink.build_pair_key(agent.id, peer_agent.id)
        try:
            link = AgentPeerLink.objects.select_related("agent_a", "agent_b").get(pair_key=pair_key)
        except AgentPeerLink.DoesNotExist as exc:
            logger.warning(
                "Peer DM attempt rejected: no link between %s and %s",
                agent.id,
                peer_agent.id,
            )
            raise PeerMessagingError(
                "No peer messaging link exists between these agents.",
                status="error",
            ) from exc

        if not link.is_enabled:
            raise PeerMessagingError(
                "Peer messaging for this link is currently disabled.",
                status="disabled",
            )

        if link.feature_flag:
            try:
                if not flag_is_active(None, link.feature_flag):
                    raise PeerMessagingError(
                        "Peer messaging feature flag is not enabled.",
                        status="disabled",
                    )
            except Exception as exc:
                logger.warning(
                    "Failed flag check for peer link %s (flag=%s): %s",
                    link.id,
                    link.feature_flag,
                    exc,
                )
                raise PeerMessagingError(
                    "Peer messaging feature flag is not available.",
                    status="disabled",
                ) from exc

        return link

    @traced("Agent Peer DM Send")
    def send_message(self, body: str) -> PeerSendResult:
        """Send a peer DM, enforcing quotas and debouncing."""
        if not body or not body.strip():
            raise PeerMessagingError("Message body cannot be empty.")

        normalized_body = body.strip()
        now = timezone.now()

        with transaction.atomic():
            state = self._lock_state(now)

            # Debounce loop prevention (~5s default)
            if state.last_message_at is not None:
                elapsed = (now - state.last_message_at).total_seconds()
                if elapsed < state.debounce_seconds:
                    retry_at = state.last_message_at + timedelta(seconds=state.debounce_seconds)
                    logger.info(
                        "Peer DM debounce hit for link %s (elapsed=%.2fs)",
                        self.link.id,
                        elapsed,
                    )
                    raise PeerMessagingError(
                        "Peer messaging suppressed to avoid a rapid loop. Wait a few seconds before retrying.",
                        status="debounced",
                        retry_at=retry_at,
                    )

            # Out of credits? schedule follow-up and exit
            if state.credits_remaining <= 0:
                logger.info(
                    "Peer DM quota exhausted for link %s; retry after %s",
                    self.link.id,
                    state.window_reset_at,
                )
                self._schedule_follow_up(self.agent.id, state.window_reset_at)
                raise PeerMessagingError(
                    "Peer messaging quota exhausted. Retry after the window resets.",
                    status="throttled",
                    retry_at=state.window_reset_at,
                )

            # Deduct a credit and record send timestamp
            state.credits_remaining -= 1
            state.last_message_at = now
            state.save(update_fields=["credits_remaining", "last_message_at", "updated_at"])

            # If credits just hit zero, queue a follow-up at reset time
            if state.credits_remaining == 0:
                self._schedule_follow_up(self.agent.id, state.window_reset_at)

            conversation = self._ensure_conversation()
            from_endpoint = self._ensure_peer_endpoint(self.agent)
            self._ensure_peer_endpoint(self.peer_agent)

            outbound_payload = {
                "_source": "agent_peer_dm",
                "direction": "outbound",
                "peer_link_id": str(self.link.id),
            }
            inbound_payload = {
                "_source": "agent_peer_dm",
                "direction": "inbound",
                "peer_link_id": str(self.link.id),
            }

            # Outgoing record for the sending agent
            PersistentAgentMessage.objects.create(
                is_outbound=True,
                from_endpoint=from_endpoint,
                conversation=conversation,
                owner_agent=self.agent,
                peer_agent=self.peer_agent,
                body=normalized_body,
                raw_payload=outbound_payload,
            )

            # Incoming record for the receiving agent
            inbound_message = PersistentAgentMessage.objects.create(
                is_outbound=False,
                from_endpoint=from_endpoint,
                conversation=conversation,
                owner_agent=self.peer_agent,
                peer_agent=self.agent,
                body=normalized_body,
                raw_payload=inbound_payload,
            )

            # Touch receiving agent for lifecycle bookkeeping
            self._touch_peer_agent(now)

            # Wake the receiving agent to process the inbound message
            transaction.on_commit(
                lambda: self._enqueue_processing(self.peer_agent.id)
            )

            logger.info(
                "Peer DM sent link=%s sender=%s receiver=%s msg=%s",
                self.link.id,
                self.agent.id,
                self.peer_agent.id,
                inbound_message.id,
            )

            return PeerSendResult(
                status="ok",
                message="Peer message delivered.",
                remaining_credits=state.credits_remaining,
                window_reset_at=state.window_reset_at,
            )

    def _lock_state(self, now) -> AgentCommPeerState:
        state, created = AgentCommPeerState.objects.select_for_update().get_or_create(
            link=self.link,
            channel=self.CHANNEL,
            defaults={
                "messages_per_window": self.link.messages_per_window,
                "window_hours": self.link.window_hours,
                "credits_remaining": self.link.messages_per_window,
                "window_reset_at": now + timedelta(hours=self.link.window_hours),
                "debounce_seconds": 5,
            },
        )

        dirty_fields: set[str] = set()

        if state.messages_per_window != self.link.messages_per_window:
            state.messages_per_window = self.link.messages_per_window
            dirty_fields.add("messages_per_window")
        if state.window_hours != self.link.window_hours:
            state.window_hours = self.link.window_hours
            dirty_fields.add("window_hours")
        if state.debounce_seconds <= 0:
            state.debounce_seconds = 5
            dirty_fields.add("debounce_seconds")

        if state.window_reset_at <= now:
            state.window_reset_at = now + timedelta(hours=state.window_hours)
            state.credits_remaining = state.messages_per_window
            dirty_fields.update({"window_reset_at", "credits_remaining"})
        elif state.credits_remaining > state.messages_per_window:
            state.credits_remaining = state.messages_per_window
            dirty_fields.add("credits_remaining")

        if dirty_fields:
            dirty_fields.add("updated_at")
            state.save(update_fields=list(dirty_fields))

        return state

    def _ensure_conversation(self) -> PersistentAgentConversation:
        if getattr(self.link, "conversation", None):
            return self.link.conversation

        conversation = PersistentAgentConversation.objects.create(
            channel=self.CHANNEL,
            address=f"peer://{self.link.pair_key}",
            display_name=f"{self.link.agent_a.name} <-> {self.link.agent_b.name}",
            is_peer_dm=True,
            peer_link=self.link,
        )
        logger.debug(
            "Created peer DM conversation %s for link %s",
            conversation.id,
            self.link.id,
        )
        self.link.conversation = conversation
        return conversation

    @staticmethod
    def _ensure_peer_endpoint(agent: PersistentAgent) -> PersistentAgentCommsEndpoint:
        address = f"peer://agent/{agent.id}"
        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            owner_agent=agent,
            channel=CommsChannel.OTHER,
            address=address,
            defaults={
                "is_primary": False,
            },
        )
        return endpoint

    def _touch_peer_agent(self, now) -> None:
        try:
            locked = (
                PersistentAgent.objects.select_for_update()
                .get(id=self.peer_agent.id)
            )
        except PersistentAgent.DoesNotExist:
            return

        updates = ["last_interaction_at"]
        locked.last_interaction_at = now
        if (
            locked.life_state == PersistentAgent.LifeState.EXPIRED
            and locked.is_active
        ):
            if locked.schedule_snapshot:
                locked.schedule = locked.schedule_snapshot
                updates.append("schedule")
            locked.life_state = PersistentAgent.LifeState.ACTIVE
            updates.append("life_state")

        locked.save(update_fields=updates)

    @staticmethod
    def _enqueue_processing(agent_id: UUID) -> None:
        from api.agent.tasks import process_agent_events_task

        process_agent_events_task.delay(str(agent_id))

    @staticmethod
    def _schedule_follow_up(agent_id: UUID, eta) -> None:
        if not eta:
            return

        eta_value = eta
        if eta_value <= timezone.now():
            eta_value = timezone.now() + timedelta(seconds=1)

        def _enqueue() -> None:
            from api.agent.tasks import process_agent_events_task

            process_agent_events_task.apply_async((str(agent_id),), eta=eta_value)

        transaction.on_commit(_enqueue)
