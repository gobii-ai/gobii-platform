import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from api.models import (
    AgentCollaborator,
    PersistentAgentStep,
    PersistentAgentSystemStep,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=AgentCollaborator)
def handle_collaborator_added(sender, instance: AgentCollaborator, created: bool, raw: bool, **kwargs) -> None:
    if raw or not created:
        return

    agent_id = instance.agent_id
    if not agent_id:
        logger.debug("Collaborator %s saved without agent; skipping prompt notification.", instance.id)
        return

    email = (getattr(instance.user, "email", "") or "").strip()
    description = f"Collaborator added: {email}" if email else "Collaborator added"
    step = PersistentAgentStep.objects.create(
        agent_id=agent_id,
        description=description,
    )

    notes_parts = [
        f"collaborator_id={instance.id}",
        f"user_id={instance.user_id}",
    ]
    if email:
        notes_parts.append(f"email={email}")
    if instance.invited_by_id:
        notes_parts.append(f"invited_by={instance.invited_by_id}")

    PersistentAgentSystemStep.objects.create(
        step=step,
        code=PersistentAgentSystemStep.Code.COLLABORATOR_ADDED,
        notes="; ".join(notes_parts),
    )

    def _enqueue_processing() -> None:
        from api.agent.tasks.process_events import process_agent_events_task

        process_agent_events_task.delay(str(agent_id))

    transaction.on_commit(_enqueue_processing)
