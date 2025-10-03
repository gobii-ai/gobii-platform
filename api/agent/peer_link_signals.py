"""Signals for agent peer link lifecycle hooks."""
from __future__ import annotations

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from api.models import AgentPeerLink

logger = logging.getLogger(__name__)


@receiver(post_save, sender=AgentPeerLink)
def handle_peer_link_created(sender, instance: AgentPeerLink, created: bool, raw: bool, **kwargs) -> None:
    if raw or not created:
        return

    agent_a = instance.agent_a
    agent_b = instance.agent_b

    if not agent_a or not agent_b:
        logger.debug(
            "Peer link %s created without both agents loaded; skipping intro automation.",
            instance.id,
        )
        return

    logger.info(
        "Peer link %s connects agent %s with agent %s; intro automation disabled.",
        instance.id,
        agent_a.id,
        agent_b.id,
    )
