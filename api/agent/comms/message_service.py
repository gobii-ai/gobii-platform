from __future__ import annotations

from uuid import UUID

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource

"""Service helpers for inbound communication messages."""

from dataclasses import dataclass
from typing import Iterable, Any

import logging
import requests

import requests
from django.core.files.base import ContentFile, File
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.db import transaction
from ..files.filespace_service import enqueue_import_after_commit

from ...models import (
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    CommsChannel,
)

from .adapters import ParsedMessage
from observability import traced
from opentelemetry import baggage
from config import settings
from util.constants.task_constants import TASKS_UNLIMITED

@dataclass
class InboundMessageInfo:
    """Info about the stored message."""

    message: PersistentAgentMessage


def _get_or_create_endpoint(channel: str, address: str) -> PersistentAgentCommsEndpoint:
    ep, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=channel,
        address=address,
    )
    return ep


def _get_or_create_conversation(channel: str, address: str, owner_agent=None) -> PersistentAgentConversation:
    conv, created = PersistentAgentConversation.objects.get_or_create(
        channel=channel,
        address=address,
        defaults={"owner_agent": owner_agent},
    )
    if owner_agent and conv.owner_agent_id is None:
        conv.owner_agent = owner_agent
        conv.save(update_fields=["owner_agent"])
    return conv


def _ensure_participant(conv: PersistentAgentConversation, ep: PersistentAgentCommsEndpoint, role: str) -> None:
    PersistentAgentConversationParticipant.objects.get_or_create(
        conversation=conv,
        endpoint=ep,
        defaults={"role": role},
    )


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


@transaction.atomic
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
            from django.utils import timezone
            from api.models import PersistentAgent
            try:
                with transaction.atomic():
                    agent_locked: PersistentAgent = (
                        PersistentAgent.objects.select_for_update().get(id=owner_id)
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
            except PersistentAgent.DoesNotExist:
                pass

            # Before triggering agent processing, check if the agent owner's
            # account is out of credits. If so, send a reply email to the sender
            # (only for email channel) and skip processing.
            should_skip_processing = False

            try:
                if channel_val == CommsChannel.EMAIL:
                    from api.models import PersistentAgent, CommsChannel as CC
                    from tasks.services import TaskCreditService

                    agent_obj = PersistentAgent.objects.filter(id=owner_id).select_related("user").first()
                    if agent_obj and agent_obj.user_id:
                        # Ensure the sender is in the agent's allow list before replying
                        if agent_obj.is_sender_whitelisted(CC.EMAIL, parsed.sender):
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
                                        properties={
                                            "agent_id": str(agent_obj.id),
                                            "agent_name": agent_obj.name,
                                            "channel": channel_val,
                                            "sender": parsed.sender,
                                        },
                                    )
                                except Exception:
                                    # Do not block on email failures
                                    logging.exception("Failed sending out-of-credits reply email")

                                # Skip processing by the agent
                                should_skip_processing = True
            except Exception:
                logging.exception("Error during out-of-credits pre-processing check")

            if not should_skip_processing:
                from api.agent.tasks import process_agent_events_task
                # Top-level trigger: no budget context provided
                process_agent_events_task.delay(str(owner_id))

        return InboundMessageInfo(message=message)

def get_agent_id_from_address(channel: CommsChannel | str, address: str) -> UUID | None:
    """
    Get the agent ID associated with a given address.

    This is a placeholder implementation that should be replaced with actual logic
    to retrieve the agent ID based on the channel and address.

    For now, it returns a hardcoded UUID for testing purposes.

    """
    channel_val = channel.value if isinstance(channel, CommsChannel) else channel
    try:
        endpoint = PersistentAgentCommsEndpoint.objects.get(channel=channel_val, address=address)
        return endpoint.owner_agent_id
    except PersistentAgentCommsEndpoint.DoesNotExist:
        return None
