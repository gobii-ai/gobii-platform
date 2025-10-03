from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentToolCall,
)

from .timeline import (
    build_processing_snapshot,
    build_tool_cluster_from_steps,
    serialize_message_event,
    serialize_processing_snapshot,
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


def _broadcast_tool_cluster(step: PersistentAgentStep) -> None:
    if not step.agent_id:
        return
    try:
        payload = build_tool_cluster_from_steps([step])
    except ValueError:
        # Step does not yet have a tool call attached
        return
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Failed to serialize tool step %s: %s", getattr(step, "id", None), exc)
        return

    _send(_group_name(step.agent_id), "timeline_event", payload)
    _broadcast_processing(step.agent)


def emit_tool_call_realtime(step: PersistentAgentStep) -> None:
    """Public helper to broadcast a tool call cluster for a fully populated step."""

    _broadcast_tool_cluster(step)


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

    def _on_commit():
        try:
            step = (
                PersistentAgentStep.objects.select_related("agent", "tool_call")
                .get(id=instance.id)
            )
        except PersistentAgentStep.DoesNotExist:  # pragma: no cover - defensive guard
            return
        emit_tool_call_realtime(step)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=PersistentAgentToolCall)
def broadcast_new_tool_call(sender, instance: PersistentAgentToolCall, created: bool, **kwargs):
    if not created:
        return
    step = instance.step
    emit_tool_call_realtime(step)


@receiver(post_save, sender=BrowserUseAgentTask)
@receiver(post_delete, sender=BrowserUseAgentTask)
def broadcast_processing_state(sender, instance: BrowserUseAgentTask, **kwargs):
    agent = None

    browser_agent_id = getattr(instance, "agent_id", None)
    if browser_agent_id:
        try:
            browser_agent = instance.agent
        except BrowserUseAgent.DoesNotExist:
            browser_agent = None
        else:
            try:
                agent = browser_agent.persistent_agent
            except PersistentAgent.DoesNotExist:
                agent = None

        if agent is None:
            agent = PersistentAgent.objects.filter(browser_use_agent_id=browser_agent_id).first()

    if agent is None:
        return
    _broadcast_processing(agent)


def _broadcast_processing(agent):
    snapshot = build_processing_snapshot(agent)
    payload = serialize_processing_snapshot(snapshot)
    _send(_group_name(agent.id), "processing_event", payload)
