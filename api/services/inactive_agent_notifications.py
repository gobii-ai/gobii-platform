import logging
from datetime import timedelta

from django.template.loader import render_to_string
from django.utils import timezone

from api.models import CommsChannel, DeliveryStatus, PersistentAgent, PersistentAgentCommsEndpoint, PersistentAgentMessage
from api.services.agent_lifecycle import build_agent_reactivation_url


logger = logging.getLogger(__name__)

INACTIVE_AUTO_REPLY_KIND = "agent_inactive_auto_reply"
INACTIVE_BLOCKED_INPUT_KIND = "agent_inactive_blocked_input"
INACTIVE_AUTO_REPLY_COOLDOWN = timedelta(hours=24)
_DELIVERED_OR_PENDING_STATUSES = {
    DeliveryStatus.QUEUED,
    DeliveryStatus.SENDING,
    DeliveryStatus.SENT,
    DeliveryStatus.DELIVERED,
}


def mark_inbound_message_blocked_while_inactive(message: PersistentAgentMessage) -> None:
    payload = dict(message.raw_payload or {})
    payload["inactive_handling"] = INACTIVE_BLOCKED_INPUT_KIND
    message.raw_payload = payload
    message.save(update_fields=["raw_payload"])


def inactive_auto_reply_body(agent: PersistentAgent) -> str:
    return (
        "I’m paused and can’t reply or take action right now. "
        f"If you manage {agent.name or 'this agent'}, reactivate me here: "
        f"{build_agent_reactivation_url(agent)} "
        "Otherwise, contact the person who manages me."
    )


def _recent_notice_exists(
    agent: PersistentAgent,
    *,
    channel: str,
    recipient_key: str,
) -> bool:
    return PersistentAgentMessage.objects.filter(
        owner_agent=agent,
        is_outbound=True,
        raw_payload__kind=INACTIVE_AUTO_REPLY_KIND,
        raw_payload__inactive_recipient_key=recipient_key,
        raw_payload__inactive_channel=channel,
        latest_status__in=_DELIVERED_OR_PENDING_STATUSES,
        timestamp__gte=timezone.now() - INACTIVE_AUTO_REPLY_COOLDOWN,
    ).exists()


def send_inactive_agent_auto_reply(
    agent: PersistentAgent,
    recipient_endpoint: PersistentAgentCommsEndpoint,
) -> bool:
    refreshed = PersistentAgent.objects.alive().filter(pk=agent.pk).only("is_active").first()
    if refreshed is None or refreshed.is_active:
        return False

    channel = str(recipient_endpoint.channel or "").strip().lower()
    if channel not in {CommsChannel.EMAIL, CommsChannel.SMS}:
        return False

    recipient_key = PersistentAgentCommsEndpoint.normalize_address(channel, recipient_endpoint.address)
    if not recipient_key or _recent_notice_exists(agent, channel=channel, recipient_key=recipient_key):
        return False

    body = inactive_auto_reply_body(agent)
    raw_payload = {
        "kind": INACTIVE_AUTO_REPLY_KIND,
        "inactive_recipient_key": recipient_key,
        "inactive_channel": channel,
    }

    if channel == CommsChannel.EMAIL:
        from api.agent.comms.email_endpoint_routing import resolve_agent_email_sender_endpoint_for_message
        from api.agent.comms.outbound_delivery import deliver_agent_email

        from_endpoint = resolve_agent_email_sender_endpoint_for_message(
            agent,
            to_endpoint=recipient_endpoint,
            cc_endpoints=None,
            has_bcc=False,
            log_context=INACTIVE_AUTO_REPLY_KIND,
        )
        if from_endpoint is None:
            logger.info("Skipping inactive email reply for agent %s: no sender endpoint.", agent.id)
            return False

        rendered_body = render_to_string(
            "emails/agent_inactive_reply.html",
            {
                "agent": agent,
                "reactivation_url": build_agent_reactivation_url(agent),
            },
        )
        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_endpoint,
            to_endpoint=recipient_endpoint,
            is_outbound=True,
            body=rendered_body,
            raw_payload={
                **raw_payload,
                "subject": "I’m paused and can’t reply right now",
            },
        )
        deliver_agent_email(message)
        return message.latest_status != DeliveryStatus.FAILED

    from api.agent.comms.email_endpoint_routing import get_agent_primary_endpoint
    from api.agent.comms.outbound_delivery import deliver_agent_sms

    from_endpoint = get_agent_primary_endpoint(agent, CommsChannel.SMS)
    if from_endpoint is None:
        logger.info("Skipping inactive SMS reply for agent %s: no sender endpoint.", agent.id)
        return False

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=from_endpoint,
        to_endpoint=recipient_endpoint,
        is_outbound=True,
        body=body,
        raw_payload=raw_payload,
    )
    deliver_agent_sms(message)
    return message.latest_status != DeliveryStatus.FAILED
