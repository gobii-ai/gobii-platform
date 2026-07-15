from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from api.models import (
    AgentCollaborator,
    AgentFileSpaceAccess,
    AgentFsNode,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    OrganizationMembership,
    OutboundMessageAttempt,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    UserPhoneNumber,
)

from .core.prompt_run_cache import (
    CONTACTS_SNAPSHOT,
    FILES_SNAPSHOT,
    MESSAGES_SNAPSHOT,
    invalidate_active_prompt_run_cache,
)


@receiver([post_save, post_delete], sender=PersistentAgentMessage)
def invalidate_message_prompt_snapshots(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(
        instance.owner_agent_id,
        MESSAGES_SNAPSHOT,
        CONTACTS_SNAPSHOT,
    )


@receiver([post_save, post_delete], sender=PersistentAgentMessageAttachment)
@receiver([post_save, post_delete], sender=OutboundMessageAttempt)
def invalidate_message_related_prompt_snapshot(sender, instance, **kwargs):
    agent_id = getattr(getattr(instance, "message", None), "owner_agent_id", None)
    invalidate_active_prompt_run_cache(agent_id, MESSAGES_SNAPSHOT)


@receiver([post_save, post_delete], sender=PersistentAgentCommsEndpoint)
def invalidate_endpoint_prompt_snapshots(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(None, MESSAGES_SNAPSHOT)


@receiver([post_save, post_delete], sender=AgentFsNode)
def invalidate_files_prompt_snapshot(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(None, FILES_SNAPSHOT)


@receiver([post_save, post_delete], sender=AgentFileSpaceAccess)
def invalidate_agent_files_prompt_snapshot(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(instance.agent_id, FILES_SNAPSHOT)


@receiver([post_save, post_delete], sender=AgentCollaborator)
@receiver([post_save, post_delete], sender=CommsAllowlistEntry)
@receiver([post_save, post_delete], sender=CommsAllowlistRequest)
def invalidate_agent_contacts_prompt_snapshot(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(instance.agent_id, CONTACTS_SNAPSHOT)


@receiver([post_save, post_delete], sender=OrganizationMembership)
@receiver([post_save, post_delete], sender=UserPhoneNumber)
def invalidate_ambiguous_contacts_prompt_snapshot(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(None, CONTACTS_SNAPSHOT)


@receiver(m2m_changed, sender=PersistentAgentMessage.cc_endpoints.through)
def invalidate_message_cc_prompt_snapshot(sender, instance, **kwargs):
    invalidate_active_prompt_run_cache(instance.owner_agent_id, CONTACTS_SNAPSHOT)
