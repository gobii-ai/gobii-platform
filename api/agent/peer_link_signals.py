"""Signals for agent peer link lifecycle hooks."""
from __future__ import annotations

import logging
from typing import Iterable

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from api.models import (
    AgentPeerLink,
    PersistentAgent,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)

logger = logging.getLogger(__name__)


def _build_charter_summary(charter: str | None) -> str:
    if not charter:
        return "No charter provided."
    cleaned = charter.strip()
    if not cleaned:
        return "No charter provided."
    if len(cleaned) > 400:
        return f"{cleaned[:397]}..."
    return cleaned


def _build_intro_step_description(owner: PersistentAgent, peer: PersistentAgent, link: AgentPeerLink) -> str:
    owner_name = owner.name or f"Agent {owner.id}"
    peer_name = peer.name or f"Agent {peer.id}"
    charter_summary = _build_charter_summary(peer.charter)
    return (
        f"Agent-to-agent link created with {peer_name} (link {link.id}).\n"
        f"Counterpart charter: {charter_summary}\n"
        "Task: send a short introduction via peer DM highlighting your name, core specialties, and how you can collaborate with them."
    )


def _iter_agents(instance: AgentPeerLink) -> Iterable[tuple[PersistentAgent, PersistentAgent]]:
    agent_a = instance.agent_a
    agent_b = instance.agent_b
    if not agent_a or not agent_b:
        return []
    return ((agent_a, agent_b), (agent_b, agent_a))


@receiver(post_save, sender=AgentPeerLink)
def handle_peer_link_created(sender, instance: AgentPeerLink, created: bool, raw: bool, **kwargs) -> None:
    if raw or not created:
        return

    pairs = list(_iter_agents(instance))
    if not pairs:
        logger.debug("Peer link %s created without both agents loaded; skipping intro steps.", instance.id)
        return

    for owner, peer in pairs:
        try:
            step = PersistentAgentStep.objects.create(
                agent=owner,
                description=_build_intro_step_description(owner, peer, instance),
            )
            PersistentAgentSystemStep.objects.create(
                step=step,
                code=PersistentAgentSystemStep.Code.PEER_LINK_CREATED,
                notes=f"Peer link {instance.id} connected agent {owner.id} with {peer.id}.",
            )
        except Exception:
            logger.exception(
                "Failed to record intro step for agent %s linked with %s.",
                owner.id,
                peer.id,
            )

    def _enqueue_events() -> None:
        from api.agent.tasks import process_agent_events_task

        unique_agent_ids = {instance.agent_a_id, instance.agent_b_id}
        for agent_id in unique_agent_ids:
            if agent_id:
                process_agent_events_task.delay(str(agent_id))

    transaction.on_commit(_enqueue_events)
