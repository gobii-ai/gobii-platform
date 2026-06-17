"""Signals for native Telegram managed bot profile synchronization."""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from api.models import PersistentAgent, PersistentAgentTelegramBotIdentity

logger = logging.getLogger(__name__)

TELEGRAM_PROFILE_SYNC_FIELDS = {
    "name",
    "avatar",
    "short_description",
    "mini_description",
    "charter",
}


@receiver(post_save, sender=PersistentAgent)
def sync_telegram_profile_after_agent_update(sender, instance: PersistentAgent, created: bool, **kwargs):
    if created:
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and TELEGRAM_PROFILE_SYNC_FIELDS.isdisjoint(set(update_fields)):
        return
    identity_id = (
        PersistentAgentTelegramBotIdentity.objects.filter(
            agent=instance,
            status=PersistentAgentTelegramBotIdentity.Status.ACTIVE,
        )
        .values_list("id", flat=True)
        .first()
    )
    if not identity_id:
        return

    def _sync() -> None:
        from api.services.telegram_bot import TelegramBotIntegrationError, sync_telegram_bot_profile

        identity = PersistentAgentTelegramBotIdentity.objects.select_related("agent").filter(id=identity_id).first()
        if identity is None:
            return
        try:
            sync_telegram_bot_profile(identity)
        except TelegramBotIntegrationError:
            logger.exception("Failed to sync Telegram profile for agent %s", instance.id)

    transaction.on_commit(_sync)
