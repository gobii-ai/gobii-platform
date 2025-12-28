from __future__ import annotations

from uuid import UUID

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.tool_costs import get_tool_credit_cost_for_channel

"""Service helpers for inbound communication messages."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Any, Tuple

import logging
import requests
from django.contrib.sites.models import Site
from django.core.exceptions import MultipleObjectsReturned
from django.core.files.base import ContentFile, File
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from ..files.filespace_service import enqueue_import_after_commit

from ...models import (
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgent,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    CommsChannel,
    DeliveryStatus,
    build_web_agent_address,
    build_web_user_address,
)

from .adapters import ParsedMessage
from .outbound_delivery import deliver_agent_sms
from observability import traced
from opentelemetry import baggage
from config import settings
from util.constants.task_constants import TASKS_UNLIMITED
from opentelemetry import trace
from util.subscription_helper import get_owner_plan

tracer = trace.get_tracer("gobii.utils")

@dataclass
class InboundMessageInfo:
    """Info about the stored message."""

    message: PersistentAgentMessage

@tracer.start_as_current_span("_get_or_create_endpoint")
def _get_or_create_endpoint(channel: str, address: str) -> PersistentAgentCommsEndpoint:
    ep, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=address,
    )
    return ep

@tracer.start_as_current_span("_get_or_create_conversation")
def _get_or_create_conversation(channel: str, address: str, owner_agent=None) -> PersistentAgentConversation:
    span = trace.get_current_span()
    span.set_attribute("channel", channel)
    span.set_attribute("address", address)

    try:
        conv, created = PersistentAgentConversation.objects.get_or_create(
            channel=channel,
            address=address,
            defaults={"owner_agent": owner_agent},
        )
    except MultipleObjectsReturned:
        span.set_attribute("get_or_create.fallback", True)
        span.set_attribute("get_or_create.error", "MultipleObjectsReturned")
        # Multiple rows exist for the same (channel, address). Pick a deterministic
        # record so ingestion can continue and emit a warning for cleanup.
        conv = (
            PersistentAgentConversation.objects
            .filter(channel=channel, address=address)
            .order_by("id")
            .first()
        )
        created = False
        logging.warning(
            "Multiple conversations found for channel=%s address=%s; using %s",
            channel,
            address,
            getattr(conv, "id", None),
        )
        if conv is None:
            raise
    if owner_agent and conv.owner_agent_id is None:
        conv.owner_agent = owner_agent
        conv.save(update_fields=["owner_agent"])
    return conv

@tracer.start_as_current_span("_ensure_participant")
def _ensure_participant(conv: PersistentAgentConversation, ep: PersistentAgentCommsEndpoint, role: str) -> None:
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conv,
        endpoint=ep,
        defaults={"role": role},
    )

@tracer.start_as_current_span("_save_attachments")
def _save_attachments(message: PersistentAgentMessage, attachments: Iterable[Any]) -> None:
    for att in attachments:
        file_obj: File | None = None
        content_type = ""
        filename = "attachment"
        max_bytes = getattr(settings, "MAX_FILE_SIZE", None)
        if hasattr(att, "read"):
            file_obj = att  # type: ignore[assignment]
            filename = getattr(att, "name", filename)
            content_type = getattr(att, "content_type", "")
            size = getattr(att, "size", None)
            # Reject oversize file-like attachments
            try:
                if max_bytes and size and int(size) > int(max_bytes):
                    logging.warning(f"File '{filename} exceeds max size of {max_bytes} bytes, skipping.")
                    continue
            except Exception:
                logging.warning(f"Could not process '{filename}' file size.")
                pass
        elif isinstance(att, str):
            try:
                resp = requests.get(att, timeout=30, allow_redirects=False)
                resp.raise_for_status()
                filename = att.rsplit("/", 1)[-1]

                # Try HEAD to check size without downloading
                if max_bytes:
                    try:
                        h = requests.head(att, allow_redirects=False, timeout=15)
                        clen = int(h.headers.get("Content-Length", "0")) if h is not None else 0
                        if clen and clen > int(max_bytes):
                            logging.warning(f"File '{filename} exceeds max size of {max_bytes} bytes, skipping.")
                            continue
                    except Exception:
                        logging.warning(f"Could not process '{filename}' file size.")
                        pass

                content = resp.content
                content_type = resp.headers.get("Content-Type", "")
                size = len(content)
                if max_bytes and size > int(max_bytes):
                    logging.warning(f"File '{filename} exceeds max size of {max_bytes} bytes, skipping.")
                    continue
                file_obj = ContentFile(content, name=filename)
            except Exception:
                continue
        else:
            continue

        if file_obj:
            if size is None:
                try:
                    size = file_obj.size
                except Exception:
                    size = 0
            PersistentAgentMessageAttachment.objects.create(
                message=message,
                file=file_obj,
                content_type=content_type,
                file_size=size,
                filename=filename,
            )

@tracer.start_as_current_span("_build_agent_detail_url")
def _build_agent_detail_url(agent) -> str:
    """Return an absolute URL to the agent's detail page."""

    current_site = Site.objects.get_current()
    protocol = "https://"
    base = f"{protocol}{current_site.domain}"
    path = reverse("agent_detail", kwargs={"pk": agent.id})
    return f"{base}{path}"

@tracer.start_as_current_span("_find_agent_endpoint")
def _find_agent_endpoint(agent, channel: str) -> PersistentAgentCommsEndpoint | None:
    """Find the agent-owned endpoint to send from for the given channel."""

    return (
        agent.comms_endpoints.filter(channel=channel, is_primary=True).first()
        or agent.comms_endpoints.filter(channel=channel).first()
    )

@tracer.start_as_current_span("_ensure_agent_web_endpoint")
def _ensure_agent_web_endpoint(agent) -> PersistentAgentCommsEndpoint:
    """Ensure the agent has a web chat endpoint for outbound messages."""

    address = build_web_agent_address(agent.id)
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.WEB,
        address=address,
        defaults={"owner_agent": agent},
    )
    if endpoint.owner_agent_id != agent.id:
        endpoint.owner_agent = agent
        endpoint.save(update_fields=["owner_agent"])
    return endpoint

@tracer.start_as_current_span("_send_daily_credit_notice")
def _send_daily_credit_notice(agent, channel: str, parsed: ParsedMessage, *,
                              sender_endpoint: PersistentAgentCommsEndpoint | None,
                              conversation: PersistentAgentConversation | None,
                              link: str) -> bool:
    """Send a daily credit limit notice back to the inbound sender."""

    plan_label = ""
    plan_id = ""
    try:
        owner = getattr(agent, "organization", None) or getattr(agent, "user", None)
        if owner:
            plan = get_owner_plan(owner)
            plan_id = str(plan.get("id") or "").strip()
            plan_label = str(plan.get("name") or plan.get("id") or "").strip()
    except Exception:
        plan_label = ""
        plan_id = ""

    message_text = (
        f"Hi there - {agent.name} has already used today's task allowance and can't reply right now. "
        f"You can increase or remove the limit here: {link}"
    )
    email_context = {"agent": agent, "link": link, "plan_label": plan_label, "plan_id": plan_id}
    analytics_source = (
        AnalyticsSource.EMAIL
        if channel == CommsChannel.EMAIL
        else AnalyticsSource.SMS
        if channel == CommsChannel.SMS
        else AnalyticsSource.WEB
        if channel == CommsChannel.WEB
        else AnalyticsSource.AGENT
    )

    try:
        if channel == CommsChannel.EMAIL:
            recipient = (parsed.sender or "").strip()
            if not recipient:
                return False
            if not agent.is_sender_whitelisted(CommsChannel.EMAIL, recipient):
                return False

            subject = f"{agent.name} hit today's task limit"
            text_body = render_to_string("emails/agent_daily_credit_notice.txt", email_context)
            html_body = render_to_string("emails/agent_daily_credit_notice.html", email_context)
            send_mail(
                subject,
                text_body,
                None,
                [recipient],
                html_message=html_body,
                fail_silently=True,
            )
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.PERSISTENT_AGENT_DAILY_CREDIT_NOTICE_SENT,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "channel": channel,
                        "recipient": recipient,
                        "plan_id": plan_id,
                        "plan_label": plan_label,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            return True

        if channel == CommsChannel.SMS:
            if not parsed.sender or sender_endpoint is None:
                return False
            if not agent.is_sender_whitelisted(CommsChannel.SMS, parsed.sender):
                return False

            from_endpoint = _find_agent_endpoint(agent, CommsChannel.SMS)
            if not from_endpoint:
                logging.info("Agent %s has no SMS endpoint for daily credit notice.", agent.id)
                return False

            outbound = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                to_endpoint=sender_endpoint,
                is_outbound=True,
                body=message_text,
                raw_payload={"kind": "daily_credit_limit_notice"},
            )
            deliver_agent_sms(outbound)
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.PERSISTENT_AGENT_DAILY_CREDIT_NOTICE_SENT,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "channel": channel,
                        "recipient": parsed.sender,
                        "plan_id": plan_id,
                        "plan_label": plan_label,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            return True

        if channel == CommsChannel.WEB:
            if not parsed.sender or sender_endpoint is None:
                return False
            if not agent.is_sender_whitelisted(CommsChannel.WEB, parsed.sender):
                return False

            agent_endpoint = _ensure_agent_web_endpoint(agent)
            conv = conversation or _get_or_create_conversation(
                CommsChannel.WEB,
                parsed.sender,
                owner_agent=agent,
            )
            if conv.owner_agent_id != agent.id:
                conv.owner_agent = agent
                conv.save(update_fields=["owner_agent"])

            _ensure_participant(
                conv,
                agent_endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.AGENT,
            )
            _ensure_participant(
                conv,
                sender_endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.HUMAN_USER,
            )

            outbound = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=agent_endpoint,
                conversation=conv,
                is_outbound=True,
                body=message_text,
                raw_payload={"source": "daily_credit_limit_notice"},
            )

            now = timezone.now()
            PersistentAgentMessage.objects.filter(pk=outbound.pk).update(
                latest_status=DeliveryStatus.DELIVERED,
                latest_sent_at=now,
                latest_delivered_at=now,
                latest_error_code="",
                latest_error_message="",
            )
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.PERSISTENT_AGENT_DAILY_CREDIT_NOTICE_SENT,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "channel": channel,
                        "recipient": parsed.sender,
                        "plan_id": plan_id,
                        "plan_label": plan_label,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            return True

    except Exception:
        logging.exception("Failed sending daily credit limit notice for agent %s", agent.id)

    return False


@transaction.atomic
@tracer.start_as_current_span("ingest_inbound_message")
def ingest_inbound_message(channel: CommsChannel | str, parsed: ParsedMessage) -> InboundMessageInfo:
    """Persist an inbound message and trigger event processing."""

    channel_val = channel.value if isinstance(channel, CommsChannel) else channel

    with traced("AGENT MSG Ingest", channel=channel_val) as span:
        from_ep = _get_or_create_endpoint(channel_val, parsed.sender)
        to_ep = _get_or_create_endpoint(channel_val, parsed.recipient)
        conv = _get_or_create_conversation(channel_val, parsed.sender, owner_agent=to_ep.owner_agent)

        _ensure_participant(conv, from_ep, PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL)
        _ensure_participant(conv, to_ep, PersistentAgentConversationParticipant.ParticipantRole.AGENT)

        agent_id = get_agent_id_from_address(channel, parsed.recipient)

        if agent_id:
            baggage.set_baggage("agent.id", agent_id)
            span.set_attribute("agent.id", str(agent_id))
        else:
            logging.warning(
                "No agent ID found for address %s on channel %s. Message may not be processed correctly.",
                parsed.recipient,
                channel_val,
            )

        message = PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=from_ep,
            conversation=conv,
            body=parsed.body,
            raw_payload=parsed.raw_payload,
            owner_agent_id=agent_id,
        )

        with traced("AGENT MSG Save Attachments") as attachment_span:
            attachment_span.set_attribute("message.id", str(message.id))
            attachment_span.set_attribute("attachments.count", len(parsed.attachments))
            _save_attachments(message, parsed.attachments)

        # Enqueue filespace import after commit, only if attachments were actually saved
        if message.attachments.exists():
            enqueue_import_after_commit(str(message.id))

        owner_id = message.owner_agent_id
        if owner_id:
            # Update last interaction timestamp and reactivate if needed
            agent_obj: PersistentAgent | None = None
            try:
                with transaction.atomic():
                    agent_locked: PersistentAgent = (
                        PersistentAgent.objects.select_for_update()
                        .select_related("user")
                        .get(id=owner_id)
                    )
                    # Update last interaction
                    agent_locked.last_interaction_at = timezone.now()
                    updates = ["last_interaction_at"]
                    # Reactivate if expired: restore schedule from snapshot if needed
                    if (
                        agent_locked.life_state == PersistentAgent.LifeState.EXPIRED
                        and agent_locked.is_active
                    ):
                        if agent_locked.schedule_snapshot:
                            agent_locked.schedule = agent_locked.schedule_snapshot
                            updates.append("schedule")
                        agent_locked.life_state = PersistentAgent.LifeState.ACTIVE
                        updates.append("life_state")
                        # Save; model will sync beat on commit due to schedule change
                        agent_locked.save(update_fields=updates)
                    else:
                        agent_locked.save(update_fields=updates)
                    agent_obj = agent_locked
            except PersistentAgent.DoesNotExist:
                agent_obj = None
            except Exception:
                logging.exception("Failed updating last interaction for agent %s", owner_id, exc_info=True)

            if agent_obj is None:
                agent_obj = PersistentAgent.objects.filter(id=owner_id).select_related("user").first()

            # Before triggering agent processing, check if the agent owner's
            # account is out of credits. If so, send a reply email to the sender
            # (only for email channel) and skip processing.
            should_skip_processing = False

            try:
                if agent_obj and agent_obj.user_id and channel_val == CommsChannel.EMAIL:
                    from tasks.services import TaskCreditService

                    if agent_obj.is_sender_whitelisted(CommsChannel.EMAIL, parsed.sender):
                        available = TaskCreditService.calculate_available_tasks(agent_obj.user)
                        if available != TASKS_UNLIMITED and available <= 0:
                                # Prepare and send out-of-credits reply via configured backend (Mailgun in prod)
                            try:
                                context = {
                                    "agent": agent_obj,
                                    "owner": agent_obj.user,
                                    "sender": parsed.sender,
                                    "subject": parsed.subject or "",
                                }
                                subject = render_to_string(
                                    "emails/agent_out_of_credits_subject.txt", context
                                ).strip() or f"Re: {parsed.subject or agent_obj.name}"
                                text_body = render_to_string(
                                    "emails/agent_out_of_credits.txt", context
                                )
                                html_body = render_to_string(
                                    "emails/agent_out_of_credits.html", context
                                )
                                recipients = {parsed.sender}
                                try:
                                    owner_email = (agent_obj.user.email or "").strip()
                                    if owner_email:
                                        recipients.add(owner_email)
                                except Exception:
                                        logging.warning(f"Failed to add owner's email to recipients for agent {agent_obj.id}", exc_info=True)

                                send_mail(
                                    subject,
                                    text_body,
                                        None,  # use DEFAULT_FROM_EMAIL
                                    list(recipients),
                                    html_message=html_body,
                                    fail_silently=True,
                                )

                                Analytics.track_event(
                                    user_id=str(agent_obj.user.id),
                                    event=AnalyticsEvent.PERSISTENT_AGENT_EMAIL_OUT_OF_CREDITS,
                                    source=AnalyticsSource.EMAIL,
                                    properties=Analytics.with_org_properties(
                                        {
                                            "agent_id": str(agent_obj.id),
                                            "agent_name": agent_obj.name,
                                            "channel": channel_val,
                                            "sender": parsed.sender,
                                        },
                                        organization=getattr(agent_obj, "organization", None),
                                    ),
                                )
                            except Exception:
                                    # Do not block on email failures
                                logging.exception("Failed sending out-of-credits reply email")

                                # Skip processing by the agent
                            should_skip_processing = True
            except Exception:
                logging.exception("Error during out-of-credits pre-processing check")

            if not should_skip_processing and agent_obj:
                try:
                    soft_target_value = agent_obj.get_daily_credit_soft_target()
                    if soft_target_value is not None:
                        remaining = agent_obj.get_daily_credit_remaining()
                        comm_tool_cost = get_tool_credit_cost_for_channel(channel_val)
                        if remaining is None or (remaining - comm_tool_cost) <= Decimal("0"):
                            should_skip_processing = True

                            try:
                                link = _build_agent_detail_url(agent_obj)
                            except Exception:
                                logging.exception(
                                    "Failed building agent detail URL for agent %s",
                                    agent_obj.id,
                                )
                                try:
                                    link = reverse("agent_detail", kwargs={"pk": agent_obj.id})
                                except Exception:
                                    link = ""

                            _send_daily_credit_notice(
                                agent_obj,
                                channel_val,
                                parsed,
                                sender_endpoint=from_ep,
                                conversation=conv,
                                link=link,
                            )
                except Exception:
                    logging.exception(
                        "Error while evaluating daily credit state for agent %s",
                        getattr(agent_obj, "id", owner_id),
                    )

            if not should_skip_processing:
                from api.agent.tasks import process_agent_events_task
                # Top-level trigger: no budget context provided
                process_agent_events_task.delay(str(owner_id))

        return InboundMessageInfo(message=message)

@tracer.start_as_current_span("get_agent_id_from_address")
def get_agent_id_from_address(channel: CommsChannel | str, address: str) -> UUID | None:
    """
    Get the agent ID associated with a given address.

    """
    channel_val = channel.value if isinstance(channel, CommsChannel) else channel
    normalized = PersistentAgentCommsEndpoint.normalize_address(channel_val, address)
    try:
        endpoint = PersistentAgentCommsEndpoint.objects.get(
            channel=channel_val,
            address__iexact=normalized,
        )
        return endpoint.owner_agent_id
    except PersistentAgentCommsEndpoint.DoesNotExist:
        return None


@tracer.start_as_current_span("inject_internal_web_message")
def inject_internal_web_message(
    agent_id: str | UUID,
    body: str,
    sender_user_id: int = -1,
    attachments: Iterable[Any] = (),
    trigger_processing: bool = True,
    eval_run_id: str | None = None,
) -> Tuple[PersistentAgentMessage, PersistentAgentConversation]:
    """
    Inject a web message for testing/evals without going through the API adapters.

    Args:
        agent_id: Target agent UUID.
        body: Message text.
        sender_user_id: Simulated user ID (default -1).
        attachments: Optional list of file-like objects or URLs.
        trigger_processing: If True, queue the processing task.
    """
    agent = PersistentAgent.objects.get(id=agent_id)
    
    sender_address = build_web_user_address(user_id=sender_user_id, agent_id=agent_id)
    agent_address = build_web_agent_address(agent.id)

    # Get/Create Endpoints
    from_ep = _get_or_create_endpoint(CommsChannel.WEB.value, sender_address)
    to_ep = _get_or_create_endpoint(CommsChannel.WEB.value, agent_address)
    
    # Ensure agent owns the target endpoint
    if to_ep.owner_agent_id != agent.id:
        to_ep.owner_agent = agent
        to_ep.save(update_fields=["owner_agent"])

    # Get/Create Conversation
    conv = _get_or_create_conversation(CommsChannel.WEB.value, sender_address, owner_agent=agent)

    # Ensure Participants
    _ensure_participant(conv, from_ep, PersistentAgentConversationParticipant.ParticipantRole.HUMAN_USER)
    _ensure_participant(conv, to_ep, PersistentAgentConversationParticipant.ParticipantRole.AGENT)

    # Create Message
    message = PersistentAgentMessage.objects.create(
        is_outbound=False,
        from_endpoint=from_ep,
        to_endpoint=to_ep,
        conversation=conv,
        body=body,
        owner_agent=agent,
        raw_payload={"source": "eval_injection", "sender_user_id": sender_user_id},
    )

    # Attachments
    if attachments:
        _save_attachments(message, attachments)
        enqueue_import_after_commit(str(message.id))

    # Trigger Processing
    if trigger_processing:
        from api.agent.tasks import process_agent_events_task
        process_agent_events_task.delay(str(agent.id), eval_run_id=eval_run_id)

    return message, conv
