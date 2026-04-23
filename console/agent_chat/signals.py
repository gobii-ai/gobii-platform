import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from api.models import (
    AgentSpawnRequest,
    AgentCollaborator,
    BrowserUseAgent,
    BrowserUseAgentTask,
    CommsAllowlistRequest,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCompletion,
    PersistentAgentHumanInputRequest,
    PersistentAgentMessage,
    PersistentAgentSecret,
    PersistentAgentStep,
    PersistentAgentSystemStep,
    PersistentAgentToolCall,
    PersistentAgentSystemMessage,
)
from api.services.signup_preview import transition_agent_to_signup_preview_waiting
from console.agent_audit.realtime import broadcast_system_message_audit, send_audit_event
from console.agent_audit.serializers import (
    serialize_completion,
    serialize_message,
    serialize_step,
    serialize_tool_call,
)
from console.agent_chat.realtime import send_user_group_event, user_profile_group_name

from .access import user_can_manage_agent_settings
from .kanban_events import persist_kanban_event
from .pending_actions import (
    expire_pending_action_requests,
    get_legacy_pending_human_input_requests,
    list_pending_action_requests,
)
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
_LAST_PROCESSING_PROFILE_STATE_BY_AGENT_ID: dict[str, bool] = {}


def _group_name(agent_id) -> str:
    return f"agent-chat-{agent_id}"


def _send(group: str, message_type: str, payload: dict) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.debug("Channel layer unavailable; skipping realtime send for group %s", group)
        return
    async_to_sync(channel_layer.group_send)(group, {"type": message_type, "payload": payload})


def emit_agent_profile_update(agent: PersistentAgent, *, processing_active: bool | None = None) -> None:
    """Broadcast latest agent identity metadata to connected chat clients."""
    if not agent or not getattr(agent, "id", None):
        return

    payload = {
        "agent_id": str(agent.id),
        "agent_name": agent.name or "Agent",
        "agent_color_hex": agent.get_display_color(),
        "agent_avatar_url": agent.get_avatar_thumbnail_url(),
        "mini_description": agent.mini_description or "",
        "short_description": agent.short_description or "",
        "signup_preview_state": agent.signup_preview_state,
        "planning_state": agent.planning_state,
        "timestamp": timezone.now().isoformat(),
    }
    if processing_active is not None:
        normalized_processing_active = bool(processing_active)
        payload["processing_active"] = normalized_processing_active
        _LAST_PROCESSING_PROFILE_STATE_BY_AGENT_ID[str(agent.id)] = normalized_processing_active
    for user_id in _resolve_profile_listener_user_ids(agent):
        _send(user_profile_group_name(user_id), "agent_profile_event", payload)


def emit_agent_planning_state_update(
    agent: PersistentAgent,
    *,
    include_pending_actions: bool = False,
) -> None:
    """Best-effort planning-state realtime update for chat/profile clients."""
    try:
        emit_agent_profile_update(agent)
    except Exception:
        logger.debug(
            "Failed to broadcast planning profile update for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )
    if not include_pending_actions:
        return
    try:
        emit_pending_action_requests_update(agent)
    except Exception:
        logger.debug(
            "Failed to broadcast planning pending-action update for agent %s",
            getattr(agent, "id", None),
            exc_info=True,
        )


def _emit_processing_profile_update_if_changed(agent: PersistentAgent, processing_active: bool) -> None:
    agent_id = str(agent.id)
    normalized_processing_active = bool(processing_active)
    previous_processing_active = _LAST_PROCESSING_PROFILE_STATE_BY_AGENT_ID.get(agent_id)
    if previous_processing_active is not None and previous_processing_active == normalized_processing_active:
        return
    emit_agent_profile_update(agent, processing_active=normalized_processing_active)


def _resolve_profile_listener_user_ids(agent: PersistentAgent) -> set[int]:
    user_ids: set[int] = set()

    if agent.organization_id:
        user_ids.update(
            OrganizationMembership.objects.filter(
                org_id=agent.organization_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).values_list("user_id", flat=True)
        )
    else:
        owner_user_id = getattr(agent, "user_id", None)
        if owner_user_id is not None:
            user_ids.add(owner_user_id)

    user_ids.update(
        AgentCollaborator.objects.filter(agent_id=agent.id).values_list("user_id", flat=True)
    )
    return user_ids


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


def emit_pending_human_input_requests_update(agent: PersistentAgent) -> None:
    if not agent or not getattr(agent, "id", None):
        return

    pending_action_requests = list_pending_action_requests(agent, None)
    payload = {
        "agent_id": str(agent.id),
        "pending_human_input_requests": get_legacy_pending_human_input_requests(pending_action_requests),
        "timestamp": timezone.now().isoformat(),
    }
    _send(_group_name(agent.id), "human_input_requests_event", payload)


def emit_pending_action_requests_update(agent: PersistentAgent) -> None:
    if not agent or not getattr(agent, "id", None):
        return

    user_model = get_user_model()
    user_ids = sorted(_resolve_profile_listener_user_ids(agent))
    viewers_by_id = user_model.objects.in_bulk(user_ids)
    timestamp = timezone.now().isoformat()
    expire_pending_action_requests(agent)
    manager_payload: list[dict] | None = None
    collaborator_payload: list[dict] | None = None
    for user_id in user_ids:
        viewer = viewers_by_id.get(user_id)
        if viewer is None:
            continue
        if user_can_manage_agent_settings(viewer, agent, allow_delinquent_personal_chat=True):
            if manager_payload is None:
                manager_payload = list_pending_action_requests(agent, viewer)
            pending_action_requests = manager_payload
        else:
            if collaborator_payload is None:
                collaborator_payload = list_pending_action_requests(agent, viewer)
            pending_action_requests = collaborator_payload
        payload = {
            "agent_id": str(agent.id),
            "pending_action_requests": pending_action_requests,
            "timestamp": timestamp,
        }
        send_user_group_event(str(agent.id), user_id, "pending_action_requests_event", payload)


def _should_audit_tool_call(tool_call: PersistentAgentToolCall | None) -> bool:
    if tool_call is None:
        return False
    return tool_call.status != "pending"


def emit_tool_call_realtime(step: PersistentAgentStep) -> None:
    """Public helper to broadcast a tool call cluster for a fully populated step."""

    _broadcast_tool_cluster(step)


def emit_tool_call_audit(step: PersistentAgentStep) -> None:
    tool_call = getattr(step, "tool_call", None)
    if not _should_audit_tool_call(tool_call):
        return
    agent_id = getattr(step, "agent_id", None)
    if not agent_id:
        return
    try:
        audit_payload = serialize_tool_call(step)
        _broadcast_audit_event(str(agent_id), audit_payload)
    except Exception:
        logger.debug("Failed to broadcast audit tool call %s", getattr(step, "id", None), exc_info=True)


@receiver(post_save, sender=PersistentAgentMessage)
def broadcast_new_message(sender, instance: PersistentAgentMessage, created: bool, **kwargs):  # noqa: D401
    if not created:
        return
    if not instance.owner_agent_id:
        return
    owner_agent_id = instance.owner_agent_id
    message_id = instance.id
    is_hidden = is_chat_hidden_message(instance)
    preview_state_transitioned = False
    if instance.is_outbound and not is_hidden:
        preview_state_transitioned = transition_agent_to_signup_preview_waiting(owner_agent_id)

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
        if preview_state_transitioned:
            try:
                emit_agent_profile_update(msg.owner_agent)
            except Exception:
                logger.debug("Failed to broadcast signup preview transition for %s", message_id, exc_info=True)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=PersistentAgentHumanInputRequest)
def broadcast_human_input_requests_updated(sender, instance: PersistentAgentHumanInputRequest, **kwargs):
    if not instance.agent_id:
        return

    def _on_commit():
        try:
            agent = PersistentAgent.objects.get(id=instance.agent_id)
        except PersistentAgent.DoesNotExist:
            return
        emit_pending_human_input_requests_update(agent)
        emit_pending_action_requests_update(agent)

    transaction.on_commit(_on_commit)


@receiver(post_delete, sender=PersistentAgentHumanInputRequest)
def broadcast_human_input_requests_deleted(sender, instance: PersistentAgentHumanInputRequest, **kwargs):
    if not instance.agent_id:
        return

    def _on_commit():
        try:
            agent = PersistentAgent.objects.get(id=instance.agent_id)
        except PersistentAgent.DoesNotExist:
            return
        emit_pending_human_input_requests_update(agent)
        emit_pending_action_requests_update(agent)

    transaction.on_commit(_on_commit)


def _broadcast_pending_action_requests_for_agent(agent_id) -> None:
    if not agent_id:
        return

    def _on_commit():
        try:
            agent = PersistentAgent.objects.get(id=agent_id)
        except PersistentAgent.DoesNotExist:
            return
        emit_pending_action_requests_update(agent)

    transaction.on_commit(_on_commit)


@receiver(post_save, sender=AgentSpawnRequest)
def broadcast_spawn_requests_updated(sender, instance: AgentSpawnRequest, **kwargs):
    _broadcast_pending_action_requests_for_agent(instance.agent_id)


@receiver(post_delete, sender=AgentSpawnRequest)
def broadcast_spawn_requests_deleted(sender, instance: AgentSpawnRequest, **kwargs):
    _broadcast_pending_action_requests_for_agent(instance.agent_id)


@receiver(post_save, sender=PersistentAgentSecret)
def broadcast_requested_secrets_updated(sender, instance: PersistentAgentSecret, **kwargs):
    _broadcast_pending_action_requests_for_agent(instance.agent_id)


@receiver(post_delete, sender=PersistentAgentSecret)
def broadcast_requested_secrets_deleted(sender, instance: PersistentAgentSecret, **kwargs):
    _broadcast_pending_action_requests_for_agent(instance.agent_id)


@receiver(post_save, sender=CommsAllowlistRequest)
def broadcast_contact_requests_updated(sender, instance: CommsAllowlistRequest, **kwargs):
    _broadcast_pending_action_requests_for_agent(instance.agent_id)


@receiver(post_delete, sender=CommsAllowlistRequest)
def broadcast_contact_requests_deleted(sender, instance: CommsAllowlistRequest, **kwargs):
    _broadcast_pending_action_requests_for_agent(instance.agent_id)


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
                .prefetch_related("human_input_requests")
                .get(id=instance.id)
            )
        except PersistentAgentStep.DoesNotExist:  # pragma: no cover - defensive guard
            return
        emit_tool_call_realtime(step)
        try:
            tool_call = getattr(step, "tool_call", None)
            if tool_call is None and not (step.description or "").startswith("Tool call"):
                step_payload = serialize_step(step)
                _broadcast_audit_event(str(step.agent_id), step_payload)
            if _should_audit_tool_call(tool_call):
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
    if not _should_audit_tool_call(instance):
        return
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


@receiver(post_save, sender=PersistentAgent)
def broadcast_agent_profile_update(sender, instance: PersistentAgent, created: bool, **kwargs):
    tracked_fields = {
        "name",
        "avatar",
        "agent_color",
        "agent_color_id",
        "mini_description",
        "short_description",
    }
    update_fields = kwargs.get("update_fields")
    if not created and update_fields is not None:
        changed_fields = {str(field) for field in update_fields}
        if tracked_fields.isdisjoint(changed_fields):
            return

    def _on_commit():
        refreshed = (
            PersistentAgent.objects
            .filter(id=instance.id)
            .only("id", "name", "avatar", "agent_color_id", "mini_description", "short_description")
            .first()
        )
        if refreshed is None:
            return
        emit_agent_profile_update(refreshed)

    transaction.on_commit(_on_commit)


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
    _emit_processing_profile_update_if_changed(agent, snapshot.active)
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
