from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from api.models import (
    BrowserUseAgentTask,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
)

from .timeline import (
    build_tool_cluster_from_steps,
    compute_processing_status,
    serialize_message_event,
)

logger = logging.getLogger(__name__)


def _group_name(agent_id) -> str:
    return f"agent-chat-{agent_id}"


def _send(group: str, message_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug("Channel layer unavailable; skipping realtime send for group %s", group)
        return
    async_to_sync(channel_layer.group_send)(group, {"type": message_type, "payload": payload})


@receiver(post_save, sender=PersistentAgentMessage)
def broadcast_new_message(sender, instance: PersistentAgentMessage, created: bool, **kwargs):  # noqa: D401
    if not created:
        return
    if not instance.owner_agent_id:
        return
    try:
        payload = serialize_message_event(instance)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Failed to serialize agent message %s: %s", instance.id, exc)
        return
    _send(_group_name(instance.owner_agent_id), "timeline_event", payload)


@receiver(post_save, sender=PersistentAgentStep)
def broadcast_new_tool_step(sender, instance: PersistentAgentStep, created: bool, **kwargs):
    if not created:
        return
    if not instance.agent_id:
        return
    try:
        instance.tool_call
    except PersistentAgentToolCall.DoesNotExist:
        return
    try:
        payload = build_tool_cluster_from_steps([instance])
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Failed to serialize tool step %s: %s", instance.id, exc)
        return
    _send(_group_name(instance.agent_id), "timeline_event", payload)

    # Also refresh processing indicator, tool steps usually signal completion
    _broadcast_processing(instance.agent)


@receiver(post_save, sender=PersistentAgentToolCall)
def broadcast_new_tool_call(sender, instance: PersistentAgentToolCall, created: bool, **kwargs):
    if not created:
        return
    step = instance.step
    if not step.agent_id:
        return
    try:
        payload = build_tool_cluster_from_steps([step])
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception(
            "Failed to serialize tool call step %s: %s",
            getattr(step, "id", None),
            exc,
        )
        return
    _send(_group_name(step.agent_id), "timeline_event", payload)

    # Tool completions also update the processing indicator
    _broadcast_processing(step.agent)


@receiver(post_save, sender=BrowserUseAgentTask)
@receiver(post_delete, sender=BrowserUseAgentTask)
def broadcast_processing_state(sender, instance: BrowserUseAgentTask, **kwargs):
    agent = getattr(getattr(instance, "agent", None), "persistent_agent", None)
    if agent is None:
        return
    _broadcast_processing(agent)


def _broadcast_processing(agent):
    state = compute_processing_status(agent)
    payload = {"active": state}
    _send(_group_name(agent.id), "processing_event", payload)
