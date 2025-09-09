"""Signal handlers to nudge IMAP IDLE runner via Redis when accounts change.

We keep it intentionally simple: on any save/delete of AgentEmailAccount,
push the account ID onto a Redis list queue. Runners BLPOP and rescan early.

This provides low-latency reaction to new/updated accounts while the runner's
periodic scan remains the safety net.
"""
from __future__ import annotations

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import AgentEmailAccount
from config.redis_client import get_redis_client

logger = logging.getLogger(__name__)

QUEUE_KEY = "imap-idle:queue"


def _notify(account_id: str) -> None:
    try:
        r = get_redis_client()
        # Fire-and-forget — runner uses BLPOP with short timeout
        r.rpush(QUEUE_KEY, account_id)
    except Exception as e:
        logger.debug("Failed to push IMAP idle notify for %s: %s", account_id, e)


@receiver(post_save, sender=AgentEmailAccount)
def on_agent_email_account_saved(sender, instance: AgentEmailAccount, created, **kwargs):
    # Always nudge; runner will evaluate eligibility. Keeps behavior simple and responsive.
    try:
        _notify(str(instance.pk))
    except Exception:
        pass


@receiver(post_delete, sender=AgentEmailAccount)
def on_agent_email_account_deleted(sender, instance: AgentEmailAccount, **kwargs):
    try:
        _notify(str(instance.pk))
    except Exception:
        pass
