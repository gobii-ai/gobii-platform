import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from api.models import (
    BrowserUseAgent,
    BrowserUseAgentTask,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentMessage,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    PersistentAgentSystemMessage,
)
from console.agent_audit.realtime import broadcast_system_message_audit, send_audit_event
from console.agent_audit.serializers import (
    serialize_completion,
    serialize_message,
    serialize_step,
    serialize_tool_call,
)

from .kanban_events import persist_kanban_event
from .timeline import (
    build_processing_snapshot,
    build_tool_cluster_from_steps,
    is_chat_hidden_message,
    serialize_kanban_event,
    serialize_message_event,
    serialize_processing_snapshot,
    serialize_thinking_event,
)

logger = logging.getLogger(__name__)
_CREDIT_EVENT_NOTES = {
    "daily_credit_limit_mid_loop",
    "daily_credit_limit_exhausted",
    "credit_insufficient_mid_loop",
    "credit_consumption_failure_mid_loop",
}


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


def _broadcast_audit_event(agent_id: str | None, payload: dict) -> None:
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
    owner_agent_id = instance.owner_agent_id
    message_id = instance.id
    is_hidden = is_chat_hidden_message(instance)

    def _on_commit():
        # Re-fetch to ensure we have committed data
        try:
            msg = PersistentAgentMessage.objects.get(id=message_id)
        except PersistentAgentMessage.DoesNotExist:
            return
        if is_hidden:
            try:
                audit_payload = serialize_message(msg)
                _broadcast_audit_event(str(owner_agent_id), audit_payload)
            except Exception:
                logger.debug("Failed to broadcast audit message for %s", message_id, exc_info=True)
            return
        try:
            payload = serialize_message_event(msg)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to serialize agent message %s: %s", message_id, exc)
            return
        _send(_group_name(owner_agent_id), "timeline_event", payload)
        try:
            audit_payload = serialize_message(msg)
            _broadcast_audit_event(str(owner_agent_id), audit_payload)
        except Exception:
            logger.debug("Failed to broadcast audit message for %s", message_id, exc_info=True)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=PersistentAgentStep)
def broadcast_new_tool_step(sender, instance: PersistentAgentStep, created: bool, **kwargs):
    if not created:
        return
    if not instance.agent_id:
        return

    def _on_commit():
        try:
            step = (
                PersistentAgentStep.objects.select_related("agent", "tool_call", "system_step")
                .get(id=instance.id)
            )
        except PersistentAgentStep.DoesNotExist:  # pragma: no cover - defensive guard
            return
        emit_tool_call_realtime(step)
        try:
            if not (step.description or "").startswith("Tool call"):
                step_payload = serialize_step(step)
                _broadcast_audit_event(str(step.agent_id), step_payload)
            if getattr(step, "tool_call", None):
                audit_payload = serialize_tool_call(step)
                _broadcast_audit_event(str(step.agent_id), audit_payload)
        except Exception:
            logger.debug("Failed to broadcast audit tool step %s", getattr(step, "id", None), exc_info=True)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=PersistentAgentToolCall)
def broadcast_new_tool_call(sender, instance: PersistentAgentToolCall, created: bool, **kwargs):
    if not created:
        return
    step = instance.step
    emit_tool_call_realtime(step)
    try:
        audit_payload = serialize_tool_call(step)
        _broadcast_audit_event(str(step.agent_id), audit_payload)
    except Exception:
        logger.debug("Failed to broadcast audit tool call %s", getattr(step, "id", None), exc_info=True)


@receiver(post_save, sender=PersistentAgentCompletion)
def broadcast_new_completion(sender, instance: PersistentAgentCompletion, created: bool, **kwargs):
    if not created:
        return
    if instance.agent_id:
        try:
            thinking_payload = serialize_thinking_event(instance)
            if thinking_payload:
                _send(_group_name(instance.agent_id), "timeline_event", thinking_payload)
        except Exception:
            logger.debug("Failed to broadcast thinking event for %s", instance.id, exc_info=True)
    try:
        audit_payload = serialize_completion(instance)
        _broadcast_audit_event(str(instance.agent_id), audit_payload)
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


@receiver(post_save, sender=PersistentAgentSystemStep)
def broadcast_credit_limit_event(sender, instance: PersistentAgentSystemStep, created: bool, **kwargs):
    if not created:
        return
    if instance.code != PersistentAgentSystemStep.Code.PROCESS_EVENTS:
        return
    if instance.notes not in _CREDIT_EVENT_NOTES:
        return
    step = instance.step
    if not step or not step.agent_id:
        return

    def _on_commit():
        payload = {
            "kind": "daily_credit_limit",
            "status": "hard_limit_blocked",
            "notes": instance.notes,
            "timestamp": step.created_at.isoformat() if step.created_at else None,
        }
        _send(_group_name(step.agent_id), "credit_event", payload)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=PersistentAgentSystemMessage)
def broadcast_system_message(sender, instance: PersistentAgentSystemMessage, created: bool, **kwargs):
    broadcast_system_message_audit(instance)


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
    try:
        send_audit_event(
            str(agent.id),
            {
                "kind": "processing_status",
                "active": snapshot.active,
                "timestamp": timezone.now().isoformat(),
            },
        )
    except Exception:
        logger.debug("Failed to broadcast processing status to audit channel for agent %s", agent.id, exc_info=True)


def broadcast_kanban_changes(agent, changes, snapshot) -> None:
    """Broadcast kanban card changes to the agent's chat timeline.

    Args:
        agent: The PersistentAgent that owns the kanban board
        changes: Sequence of KanbanCardChange objects
        snapshot: KanbanBoardSnapshot with current board state
    """
    if not changes or not snapshot:
        return
    if not agent or not agent.id:
        return

    try:
        agent_name = getattr(agent, "name", None) or "Agent"
        # Use first name if available
        if " " in agent_name:
            agent_name = agent_name.split()[0]
        payload = serialize_kanban_event(agent_name, changes, snapshot)
    except Exception:
        logger.debug(
            "Failed to serialize kanban changes for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )
        return

    try:
        _send(_group_name(agent.id), "timeline_event", payload)
    except Exception:
        logger.debug(
            "Failed to broadcast kanban changes for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )

    try:
        persist_kanban_event(agent, payload)
    except Exception:
        logger.debug(
            "Failed to persist kanban changes for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )
