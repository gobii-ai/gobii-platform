"""Celery task to soft-expire inactive free-plan agents."""

import logging
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from waffle import switch_is_active
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.sites.models import Site
from api.models import PersistentAgent, PersistentAgentMessage, CommsChannel

from constants.feature_flags import AGENT_SOFT_EXPIRATION
from constants.plans import PlanNames


logger = logging.getLogger(__name__)


def _is_free_plan_for_agent(agent) -> bool:
    """Return True if the owning account for this agent is on the free plan."""
    # Org-owned agents use the organization's plan if available
    if agent.organization_id:
        # Guard against odd states but avoid masking real errors
        plan = getattr(getattr(agent, "organization", None), "plan", PlanNames.FREE)
        return (plan or PlanNames.FREE) == PlanNames.FREE

    # User-owned agents: consult UserBilling.subscription
    try:
        sub = agent.user.billing.subscription  # reverse OneToOne may raise DoesNotExist
        return (sub or PlanNames.FREE) == PlanNames.FREE
    except (AttributeError, ObjectDoesNotExist) as e:
        logger.warning("No billing record for user %s while checking plan; defaulting to FREE. err=%s", getattr(agent.user, 'id', None), e)
        return True


def _within_downgrade_grace(agent) -> bool:
    """Apply 48h grace after user downgrade to free plan (user-owned only)."""
    if agent.organization_id:
        return False
    try:
        downgraded_at = agent.user.billing.downgraded_at  # may raise DoesNotExist
    except (AttributeError, ObjectDoesNotExist) as e:
        logger.debug("No downgraded_at (no billing) for user %s; no grace applies. err=%s", getattr(agent.user, 'id', None), e)
        return False
    if not downgraded_at:
        return False
    try:
        return timezone.now() < (downgraded_at + timedelta(hours=settings.AGENT_SOFT_EXPIRATION_DOWNGRADE_GRACE_HOURS))
    except (TypeError, ValueError) as e:
        logger.warning("Invalid grace hours setting; using default. err=%s", e)
        return timezone.now() < (downgraded_at + timedelta(hours=48))


def _get_agent_sending_endpoint(agent, channel: CommsChannel) -> str | None:
    """Return the agent-owned endpoint to send from for a given channel.

    Preference order: primary endpoint for channel, then any endpoint for channel.
    Returns None if the agent has no endpoint for that channel.
    """
    from_ep = agent.comms_endpoints.filter(channel=channel, is_primary=True).first()
    if not from_ep:
        from_ep = agent.comms_endpoints.filter(channel=channel).first()

    return from_ep

def _send_sleep_notification(agent) -> None:
    """Send the friendly sleep notification via the user's preferred channel."""

    # Do not notify for manually paused agents
    if not agent.is_active:
        return

    ep = agent.preferred_contact_endpoint
    if not ep:
        logger.info("Agent %s has no preferred contact endpoint; skipping sleep notification.", agent.id)
        return

    current_site = Site.objects.get_current()
    protocol = "https://"                       # your outbound scheme
    base = f"{protocol}{current_site.domain}"
    upgrade_link = f"{base}/subscribe/startup/"

    now = timezone.now()
    subject = "I’m going to sleep for now 💤"
    body_email = (
        "<p>Since I haven’t heard from you in a while, I’m going to take a nap to save resources.</p>"
        "<p>Need me? Just reply to this message to wake me up anytime.</p>"
        f"<p>Want agents that never sleep? <a href=\"{upgrade_link}\">Upgrade to Pro</a>.</p>"
        f"<p>Best,<br>{agent.name}</p>"
    )
    body_sms = (
        "I haven’t heard from you lately, so I’m going to sleep. "
        f"Text me to wake me anytime. Upgrade to Pro for agents that never sleep: {upgrade_link}"
    )

    if ep.channel == CommsChannel.EMAIL:
        # From agent's primary email endpoint to user email endpoint
        from_ep = _get_agent_sending_endpoint(agent, CommsChannel.EMAIL)
        if not from_ep:
            logger.info(f"Agent {agent.id} has no email endpoint; cannot send sleep notification.")
        msg = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_ep,
            to_endpoint=ep,
            is_outbound=True,
            body=body_email,
            raw_payload={"subject": subject, "kind": "agent_sleep_notice"},
        )
        from api.agent.comms.outbound_delivery import deliver_agent_email
        deliver_agent_email(msg)
    elif ep.channel == CommsChannel.SMS:
        from_ep = _get_agent_sending_endpoint(agent, CommsChannel.SMS)
        if not from_ep:
            logger.info(f"Agent {agent.id} has no SMS endpoint; cannot send sleep notification.")
        msg = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_ep,
            to_endpoint=ep,
            is_outbound=True,
            body=body_sms,
            raw_payload={"kind": "agent_sleep_notice"},
        )
        from api.agent.comms.outbound_delivery import deliver_agent_sms
        deliver_agent_sms(msg)
    else:
        # Only supporting email/SMS for now
        logger.info("Agent %s preferred endpoint channel %s not supported for sleep notification.", agent.id, ep.channel)
        return

    # Mark notification sent
    agent.sleep_email_sent_at = now
    agent.save(update_fields=["sleep_email_sent_at"])

@shared_task(name="api.agent.tasks.soft_expire_inactive_agents")
def soft_expire_inactive_agents_task() -> int:
    """Scan for eligible agents and soft-expire them. Returns count expired."""
    if not switch_is_active(AGENT_SOFT_EXPIRATION):
        logger.info("Soft-expiration switch disabled; skipping run.")
        return 0

    now = timezone.now()
    cutoff = now - timedelta(days=settings.AGENT_SOFT_EXPIRATION_INACTIVITY_DAYS)

    # Eligible: active life_state, schedule present, is_active True, free plan, not within downgrade grace
    qs = (
        PersistentAgent.objects
        .select_related("user", "user__billing", "organization")
        .filter(life_state=PersistentAgent.LifeState.ACTIVE)
        .filter(is_active=True)
        .exclude(schedule__isnull=True)
        .exclude(schedule="")
    )

    expired_count = 0
    for agent in qs.iterator(chunk_size=200):
        try:
            last_ts = agent.last_interaction_at or agent.created_at
            if last_ts > cutoff:
                continue
            if not _is_free_plan_for_agent(agent):
                continue
            if _within_downgrade_grace(agent):
                continue
            # Expire within a transaction/lock
            with transaction.atomic():
                locked_agent = type(agent).objects.select_for_update().get(pk=agent.pk)
                # Re-evaluate under lock
                last_ts_locked = locked_agent.last_interaction_at or locked_agent.created_at
                if (
                        locked_agent.life_state != locked_agent.LifeState.ACTIVE
                        or not locked_agent.is_active
                        or not locked_agent.schedule
                        or last_ts_locked > cutoff
                        or not _is_free_plan_for_agent(locked_agent)
                        or _within_downgrade_grace(locked_agent)
                ):
                    continue

                # Snapshot schedule for restoration (best-effort) and clear active schedule
                locked_agent.schedule_snapshot = locked_agent.schedule

                # Clear schedule; model.save will sync RedBeat (removal) after commit
                locked_agent.schedule = ""
                locked_agent.life_state = locked_agent.LifeState.EXPIRED
                locked_agent.last_expired_at = now
                locked_agent.save(update_fields=["schedule_snapshot", "schedule", "life_state", "last_expired_at"])

                # Send notification (skip if manually paused)
                try:
                    _send_sleep_notification(locked_agent)
                except Exception as ne:
                    logger.error("Failed sending sleep notification for agent %s: %s", locked_agent.id, ne)

                expired_count += 1
        except Exception as e:
            logger.error("Soft-expiration loop error for agent %s: %s", getattr(agent, "id", "?"), e)
            continue

    logger.info("Soft-expiration completed; expired %d agents.", expired_count)
    return expired_count
