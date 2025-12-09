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
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
)
from console.agent_audit.realtime import send_audit_event
from console.agent_audit.serializers import serialize_completion, serialize_message, serialize_tool_call

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


def _broadcast_audit_event(agent_id: str | None, payload: dict, timestamp=None) -> None:
    if not agent_id:
        return
    send_audit_event(agent_id, payload)


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
    try:
        audit_payload = serialize_message(instance)
        _broadcast_audit_event(str(instance.owner_agent_id), audit_payload, instance.timestamp)
    except Exception:
        logger.debug("Failed to broadcast audit message for %s", instance.id, exc_info=True)


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
        try:
            if getattr(step, "tool_call", None):
                audit_payload = serialize_tool_call(step)
                _broadcast_audit_event(str(step.agent_id), audit_payload, step.created_at)
        except Exception:
            logger.debug("Failed to broadcast audit tool step %s", getattr(step, "id", None), exc_info=True)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=PersistentAgentToolCall)
def broadcast_new_tool_call(sender, instance: PersistentAgentToolCall, created: bool, **kwargs):
    if not created:
        return
    step = instance.step
    emit_tool_call_realtime(step)


@receiver(post_save, sender=PersistentAgentCompletion)
def broadcast_new_completion(sender, instance: PersistentAgentCompletion, created: bool, **kwargs):
    if not created:
        return
    try:
        audit_payload = serialize_completion(instance)
        _broadcast_audit_event(str(instance.agent_id), audit_payload, instance.created_at)
    except Exception:
        logger.debug("Failed to broadcast audit completion %s", getattr(instance, "id", None), exc_info=True)


@receiver(post_save, sender=PersistentAgentSystemStep)
def broadcast_run_start(sender, instance: PersistentAgentSystemStep, created: bool, **kwargs):
    if not created:
        return
    if instance.code != PersistentAgentSystemStep.Code.PROCESS_EVENTS:
        return
    try:
        payload = {
            "run_id": str(instance.step_id),
            "kind": "run_started",
            "timestamp": instance.step.created_at.isoformat() if instance.step else None,
            "sequence": (
                PersistentAgentSystemStep.objects.filter(
                    step__agent_id=instance.step.agent_id if instance.step else None,
                    code=PersistentAgentSystemStep.Code.PROCESS_EVENTS,
                    step__created_at__lte=getattr(instance.step, "created_at", None),
                ).count()
                if instance.step_id
                else None
            ),
        }
        send_audit_event(str(instance.step.agent_id), payload)
    except Exception:
        logger.debug("Failed to broadcast audit run start %s", getattr(instance, "step_id", None), exc_info=True)


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
