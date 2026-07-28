from datetime import timedelta
from typing import Callable

from django.db import transaction
from django.utils import timezone

from api.models import CommsChannel, DeliveryStatus, PersistentAgent, PersistentAgentCommsEndpoint, PersistentAgentMessage
from api.services.agent_lifecycle import build_agent_reactivation_url
from api.services.channel_auto_replies import prepare_channel_auto_reply

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


def send_inactive_notice_once(
    agent: PersistentAgent,
    *,
    channel: str,
    recipient_key: str,
    prepare: Callable[[dict[str, str]], Callable[[], bool] | None],
) -> bool:
    """Persist a dedupe claim under the agent lock, then deliver without holding it."""
    with transaction.atomic():
        locked = PersistentAgent.objects.alive().select_for_update().filter(pk=agent.pk).first()
        if locked is None or locked.is_active:
            return False
        if PersistentAgentMessage.objects.filter(
            owner_agent=locked,
            is_outbound=True,
            raw_payload__kind=INACTIVE_AUTO_REPLY_KIND,
            raw_payload__inactive_recipient_key=recipient_key,
            raw_payload__inactive_channel=channel,
            latest_status__in=_DELIVERED_OR_PENDING_STATUSES,
            timestamp__gte=timezone.now() - INACTIVE_AUTO_REPLY_COOLDOWN,
        ).exists():
            return False
        deliver = prepare(
            {
                "inactive_recipient_key": recipient_key,
                "inactive_channel": channel,
            }
        )
    return deliver() if deliver is not None else False


def send_inactive_agent_auto_reply(
    agent: PersistentAgent,
    recipient_endpoint: PersistentAgentCommsEndpoint,
) -> bool:
    channel = str(recipient_endpoint.channel or "").strip().lower()
    if channel not in {CommsChannel.EMAIL, CommsChannel.SMS}:
        return False

    recipient_key = PersistentAgentCommsEndpoint.normalize_address(channel, recipient_endpoint.address)
    if not recipient_key:
        return False

    reactivation_url = build_agent_reactivation_url(agent)
    return send_inactive_notice_once(
        agent,
        channel=channel,
        recipient_key=recipient_key,
        prepare=lambda metadata: prepare_channel_auto_reply(
            agent,
            recipient_endpoint,
            kind=INACTIVE_AUTO_REPLY_KIND,
            subject="I’m paused and can’t reply right now",
            email_template="emails/agent_billing_paused_reply.html",
            email_context={
                "agent": agent,
                "intro_text": "I’m paused and can’t reply or take action right now.",
                "detail_text": (
                    f"If you manage {agent.name or 'this agent'}, you can reactivate me below. "
                    "Otherwise, contact the person who manages me."
                ),
                "action_url": reactivation_url,
                "action_label": "Reactivate agent",
            },
            sms_body=inactive_auto_reply_body(agent),
            metadata=metadata,
        ),
    )
