import logging

from django.template.loader import render_to_string

from api.models import CommsChannel, DeliveryStatus, PersistentAgentMessage


logger = logging.getLogger(__name__)


def send_channel_auto_reply(
    agent,
    recipient_endpoint,
    *,
    kind: str,
    subject: str,
    email_template: str,
    email_context: dict,
    sms_body: str,
    metadata: dict | None = None,
) -> bool:
    channel = str(recipient_endpoint.channel or "").strip().lower()
    payload = {"kind": kind, **(metadata or {})}

    if channel == CommsChannel.EMAIL:
        from api.agent.comms.email_endpoint_routing import resolve_agent_email_sender_endpoint_for_message
        from api.agent.comms.outbound_delivery import deliver_agent_email

        from_endpoint = resolve_agent_email_sender_endpoint_for_message(
            agent,
            to_endpoint=recipient_endpoint,
            cc_endpoints=None,
            has_bcc=False,
            log_context=kind,
        )
        if from_endpoint is None:
            logger.info("Skipping %s email for agent %s: no sender endpoint.", kind, agent.id)
            return False
        body = render_to_string(email_template, email_context)
        deliver = deliver_agent_email
        payload["subject"] = subject
    elif channel == CommsChannel.SMS:
        from api.agent.comms.email_endpoint_routing import get_agent_primary_endpoint
        from api.agent.comms.outbound_delivery import deliver_agent_sms

        from_endpoint = get_agent_primary_endpoint(agent, CommsChannel.SMS)
        if from_endpoint is None:
            logger.info("Skipping %s SMS for agent %s: no sender endpoint.", kind, agent.id)
            return False
        body = sms_body
        deliver = deliver_agent_sms
    else:
        return False

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=from_endpoint,
        to_endpoint=recipient_endpoint,
        is_outbound=True,
        body=body,
        raw_payload=payload,
    )
    deliver(message)
    return message.latest_status != DeliveryStatus.FAILED
