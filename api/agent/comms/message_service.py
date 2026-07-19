from uuid import UUID
from urllib.parse import urlparse

from util.analytics import Analytics
from util.tool_costs import get_tool_credit_cost_for_channel

"""Service helpers for inbound communication messages."""

from dataclasses import dataclass
from time import monotonic
from typing import Any, Iterable, MutableMapping, Tuple

import logging
import mimetypes
import os
import requests
from django.core.exceptions import MultipleObjectsReturned, ValidationError
from django.core.files.base import ContentFile, File
from django.db import DatabaseError, transaction
from django.utils import timezone
from api.agent.core.processing_flags import bump_human_inbound_generation
from ..files.filespace_service import enqueue_import_after_commit, import_message_attachments_to_filespace

from ...models import (
    PersistentAgentInboundWebhook,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversation,
    PersistentAgent,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentMessageAttachment,
    CommsChannel,
    UserPhoneNumber,
    build_inbound_webhook_agent_address,
    build_inbound_webhook_sender_address,
    build_web_agent_address,
    build_web_user_address,
    parse_web_user_address,
)
from api.services.system_settings import get_max_file_size
from api.services.billing_pause_notifications import is_billing_execution_pause_reason, send_billing_pause_auto_reply
from api.services.owner_execution_pause import get_owner_execution_pause_state, resolve_agent_owner
from marketing_events.custom_events import ConfiguredCustomEvent, emit_configured_custom_capi_event

from .adapters import ParsedMessage
from .attachment_filters import is_signature_image_attachment
from .rejected_attachments import build_rejected_attachment_metadata
from .email_endpoint_routing import get_agent_primary_endpoint
from .message_reads import READ_SOURCE_INBOUND_REPLY, mark_latest_visible_outbound_message_read_before, resolve_inbound_read_user
from observability import traced
from opentelemetry import baggage
from config import settings
from util.constants.task_constants import TASKS_UNLIMITED
from opentelemetry import trace
from util.urls import build_agent_detail_url, build_site_url

tracer = trace.get_tracer("gobii.utils")

@dataclass
class InboundMessageInfo:
    """Info about the stored message."""

    message: PersistentAgentMessage


@dataclass
class _ProcessingDispatch:
    """Post-commit attachment and processing dispatch state."""

    owner_id: UUID
    channel: str
    trigger_processing: bool
    is_human_input: bool = True
    message_id: str | None = None
    has_attachments: bool = False
    filespace_import_mode: str = "sync"
    should_skip_processing: bool = True
    persisted_at: float = 0.0
    ready: bool = False

    def dispatch(self) -> None:
        if not self.ready:
            return

        with tracer.start_as_current_span("ingest.dispatch_after_commit") as span:
            span.set_attributes({
                "persistent_agent.id": str(self.owner_id),
                "message.id": self.message_id or "",
                "message.has_attachments": self.has_attachments,
                "processing.skipped": self.should_skip_processing,
                "message.persist_to_dispatch_ms": round(
                    (monotonic() - self.persisted_at) * 1000,
                    3,
                ),
            })

            is_interactive = self.channel == CommsChannel.WEB
            if self.has_attachments and self.message_id:
                self._dispatch_attachment_import()

            if not self.trigger_processing:
                return

            if self.should_skip_processing:
                return
            inbound_generation = None
            if self.is_human_input:
                inbound_generation = bump_human_inbound_generation(self.owner_id)

            from api.agent.tasks import enqueue_interactive_process_agent_events, process_agent_events_task

            if is_interactive:
                enqueue_interactive_process_agent_events(
                    str(self.owner_id),
                    inbound_generation=inbound_generation,
                )
            else:
                kwargs = (
                    {"inbound_generation": inbound_generation}
                    if inbound_generation is not None
                    else {}
                )
                process_agent_events_task.delay(str(self.owner_id), **kwargs)

    def _dispatch_attachment_import(self) -> None:
        if self.filespace_import_mode != "sync":
            enqueue_import_after_commit(self.message_id)
            return
        try:
            import_message_attachments_to_filespace(self.message_id)
        except Exception:
            logging.exception(
                "Failed synchronous filespace import for message %s",
                self.message_id,
            )


def _is_owner_sender(agent: PersistentAgent, channel: CommsChannel | str, sender: str | None) -> bool:
    channel_val = channel.value if isinstance(channel, CommsChannel) else str(channel)
    normalized_sender = PersistentAgentCommsEndpoint.normalize_address(channel_val, sender)
    if not normalized_sender:
        return False

    if channel_val == CommsChannel.WEB:
        sender_user_id, sender_agent_id = parse_web_user_address(normalized_sender)
        return sender_user_id == agent.user_id and sender_agent_id == str(agent.id)

    if channel_val == CommsChannel.EMAIL:
        owner_email = PersistentAgentCommsEndpoint.normalize_address(channel_val, getattr(agent.user, "email", None))
        return bool(owner_email and normalized_sender == owner_email)

    if channel_val == CommsChannel.SMS:
        return UserPhoneNumber.objects.filter(
            user_id=agent.user_id,
            is_verified=True,
            phone_number__iexact=normalized_sender,
        ).exists()

    return False


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

_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
}

def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = (parsed.path or "").rsplit("/", 1)[-1]
    return name or "attachment"

def _normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()

def _extension_for_content_type(content_type: str) -> str:
    normalized = _normalize_content_type(content_type)
    if not normalized:
        return ""
    ext = _CONTENT_TYPE_EXTENSIONS.get(normalized)
    if ext:
        return ext
    guessed = mimetypes.guess_extension(normalized) or ""
    if guessed == ".jpe":
        return ".jpg"
    return guessed

def _append_extension(filename: str, content_type: str) -> str:
    if not content_type:
        return filename
    _, ext = os.path.splitext(filename)
    if ext:
        return filename
    guessed = _extension_for_content_type(content_type)
    if not guessed:
        return filename
    return f"{filename}{guessed}"



def _is_twilio_media_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname or parsed.hostname.lower() != "api.twilio.com":
        return False
    path = parsed.path or ""
    return "/Media/" in path

def _should_skip_signature_attachment(filename: str, content_type: str) -> bool:
    if is_signature_image_attachment(filename, content_type):
        logging.debug("Skipping signature image attachment '%s'.", filename)
        return True
    return False


def _append_rejected_attachments_to_message(
    message: PersistentAgentMessage,
    rejected_attachments: Iterable[dict[str, Any]],
) -> None:
    if message.is_outbound:
        return

    rejected_list = [item for item in rejected_attachments if isinstance(item, dict)]
    if not rejected_list:
        return

    payload = message.raw_payload if isinstance(message.raw_payload, dict) else {}
    existing_raw = payload.get("rejected_attachments")
    existing = [item for item in existing_raw if isinstance(item, dict)] if isinstance(existing_raw, list) else []

    next_payload = dict(payload)
    next_payload["rejected_attachments"] = existing + rejected_list
    message.raw_payload = next_payload
    message.save(update_fields=["raw_payload"])


def _get_rejected_attachment_channel(message: PersistentAgentMessage) -> str:
    if message.from_endpoint and message.from_endpoint.channel:
        return message.from_endpoint.channel
    if message.to_endpoint and message.to_endpoint.channel:
        return message.to_endpoint.channel
    if message.conversation and message.conversation.channel:
        return message.conversation.channel
    return "unknown"


@tracer.start_as_current_span("_save_attachments")
def _save_attachments(message: PersistentAgentMessage, attachments: Iterable[Any]) -> None:
    max_bytes = get_max_file_size()
    channel = _get_rejected_attachment_channel(message)
    rejected_attachments: list[dict[str, Any]] = []
    for att in attachments:
        file_obj: File | None = None
        content_type = ""
        filename = "attachment"
        size = None
        url = None
        content_type_hint = ""
        if hasattr(att, "read"):
            file_obj = att  # type: ignore[assignment]
            filename = getattr(att, "name", filename)
            content_type = getattr(att, "content_type", "")
            size = getattr(att, "size", None)
            # Reject oversize file-like attachments
            try:
                if max_bytes and size and int(size) > int(max_bytes):
                    logging.warning(f"File '{filename}' exceeds max size of {max_bytes} bytes, skipping.")
                    rejected_attachments.append(
                        build_rejected_attachment_metadata(
                            filename=filename,
                            channel=channel,
                            limit_bytes=max_bytes,
                            reason_code="too_large",
                            size_bytes=int(size),
                        )
                    )
                    continue
            except Exception:
                logging.warning(f"Could not process '{filename}' file size.")
                pass
            if _should_skip_signature_attachment(filename, content_type):
                continue
        elif isinstance(att, dict):
            url = att.get("url") or att.get("media_url")
            if not isinstance(url, str) or not url:
                continue
            filename = att.get("filename") or filename
            content_type_hint = att.get("content_type") or ""
            if _should_skip_signature_attachment(filename, content_type_hint):
                continue
        elif isinstance(att, str):
            url = att
        else:
            continue

        if url:
            try:
                auth = None
                if _is_twilio_media_url(url):
                    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
                        auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
                    else:
                        logging.warning(
                            "Twilio media URL provided but TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not configured; "
                            "skipping download."
                        )
                        continue

                if filename == "attachment":
                    filename = _filename_from_url(url)
                if _should_skip_signature_attachment(filename, content_type_hint):
                    continue

                # Try HEAD to check size before downloading
                if max_bytes:
                    try:
                        h = requests.head(url, allow_redirects=True, timeout=15, auth=auth)
                        clen = int(h.headers.get("Content-Length", "0")) if h is not None else 0
                        if clen and clen > int(max_bytes):
                            logging.warning(f"File '{filename}' exceeds max size of {max_bytes} bytes, skipping.")
                            rejected_attachments.append(
                                build_rejected_attachment_metadata(
                                    filename=filename,
                                    channel=channel,
                                    limit_bytes=max_bytes,
                                    reason_code="too_large",
                                    size_bytes=clen,
                                )
                            )
                            continue
                    except Exception:
                        logging.warning(f"Could not process '{filename}' file size.")
                        pass

                resp = requests.get(url, timeout=30, allow_redirects=True, auth=auth)
                resp.raise_for_status()

                content = resp.content
                content_type = resp.headers.get("Content-Type", "") or content_type_hint
                filename = _append_extension(filename, content_type_hint or content_type)
                size = len(content)
                if max_bytes and size > int(max_bytes):
                    logging.warning(f"File '{filename}' exceeds max size of {max_bytes} bytes, skipping.")
                    rejected_attachments.append(
                        build_rejected_attachment_metadata(
                            filename=filename,
                            channel=channel,
                            limit_bytes=max_bytes,
                            reason_code="too_large",
                            size_bytes=size,
                        )
                    )
                    continue
                if _should_skip_signature_attachment(filename, content_type):
                    continue
                file_obj = ContentFile(content, name=filename)
            except Exception as exc:
                logging.warning("Failed to download attachment from '%s': %s", url, exc)
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
    _append_rejected_attachments_to_message(message, rejected_attachments)

def _build_site_url(path: str) -> str:
    """Return an absolute URL for a site-relative path."""
    return build_site_url(path)

@tracer.start_as_current_span("_build_agent_detail_url")
def _build_agent_detail_url(agent) -> str:
    """Return an absolute URL to the agent's detail page."""
    return build_agent_detail_url(agent.id)

@tracer.start_as_current_span("_find_agent_endpoint")
def _find_agent_endpoint(agent, channel: str) -> PersistentAgentCommsEndpoint | None:
    """Find the agent-owned endpoint to send from for the given channel."""
    return get_agent_primary_endpoint(agent, channel)

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


@tracer.start_as_current_span("_ensure_agent_inbound_webhook_endpoint")
def _ensure_agent_inbound_webhook_endpoint(agent) -> PersistentAgentCommsEndpoint:
    """Ensure the agent has an OTHER-channel endpoint for inbound webhook routing."""

    address = build_inbound_webhook_agent_address(agent.id)
    endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.OTHER,
        address=address,
        defaults={"owner_agent": agent, "is_primary": True},
    )
    updates = []
    if endpoint.owner_agent_id != agent.id:
        endpoint.owner_agent = agent
        updates.append("owner_agent")
    if not endpoint.is_primary:
        endpoint.is_primary = True
        updates.append("is_primary")
    if updates:
        endpoint.save(update_fields=updates)
    return endpoint

@transaction.atomic
@tracer.start_as_current_span("ingest_inbound_message")
def ingest_inbound_message(
    channel: CommsChannel | str,
    parsed: ParsedMessage,
    filespace_import_mode: str = "sync",
    trigger_processing: bool = True,
    prioritize_processing_dispatch: bool = False,
) -> InboundMessageInfo:
    """Persist an inbound message and trigger event processing."""

    channel_val = channel.value if isinstance(channel, CommsChannel) else channel
    is_inbound_webhook = (
        channel_val == CommsChannel.OTHER
        and isinstance(parsed.raw_payload, dict)
        and str(parsed.raw_payload.get("source_kind", "")).strip().lower() == "webhook"
    )

    with traced("AGENT MSG Ingest", channel=channel_val) as span:
        from_ep = _get_or_create_endpoint(channel_val, parsed.sender)
        to_ep = _get_or_create_endpoint(channel_val, parsed.recipient)
        conversation_address = parsed.conversation_address or parsed.sender
        conv = _get_or_create_conversation(channel_val, conversation_address, owner_agent=to_ep.owner_agent)

        _ensure_participant(conv, from_ep, PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL)
        _ensure_participant(conv, to_ep, PersistentAgentConversationParticipant.ParticipantRole.AGENT)

        agent_id = get_agent_id_from_address(channel, parsed.recipient)

        processing_dispatch = None
        if agent_id:
            processing_dispatch = _ProcessingDispatch(
                owner_id=agent_id,
                channel=channel_val,
                trigger_processing=trigger_processing,
                is_human_input=not is_inbound_webhook,
            )
        if processing_dispatch and prioritize_processing_dispatch:
            # Register before message creation so realtime post-save callbacks cannot
            # delay interactive worker dispatch after the transaction commits.
            transaction.on_commit(processing_dispatch.dispatch, robust=True)

        if agent_id:
            baggage.set_baggage("agent.id", agent_id)
            span.set_attribute("agent.id", str(agent_id))
        else:
            logging.warning(
                "No agent ID found for address %s on channel %s. Message may not be processed correctly.",
                parsed.recipient,
                channel_val,
            )

        with traced("AGENT MSG Persist") as persist_span:
            message = PersistentAgentMessage.objects.create(
                is_outbound=False,
                from_endpoint=from_ep,
                conversation=conv,
                body=parsed.body,
                raw_payload=parsed.raw_payload,
                owner_agent_id=agent_id,
            )
            message_persisted_at = monotonic()
            persist_span.set_attribute("message.id", str(message.id))
        if channel_val in {CommsChannel.WEB, CommsChannel.EMAIL, CommsChannel.SMS}:
            agent_obj = PersistentAgent.objects.filter(id=message.owner_agent_id).first()
            read_user = resolve_inbound_read_user(agent_obj, channel_val, parsed.sender)
            if read_user is not None:
                mark_latest_visible_outbound_message_read_before(
                    agent_id=message.owner_agent_id,
                    before=message.timestamp,
                    user=read_user,
                    conversation_id=message.conversation_id,
                    recipient_endpoint_id=message.from_endpoint_id,
                    source=READ_SOURCE_INBOUND_REPLY,
                )

        try:
            from api.agent.comms.human_input_requests import resolve_human_input_request_for_message

            resolve_human_input_request_for_message(message)
        except (DatabaseError, ValidationError, ValueError):
            logging.exception(
                "Failed resolving human input request for inbound message %s",
                getattr(message, "id", None),
            )

        with traced("AGENT MSG Save Attachments") as attachment_span:
            attachment_span.set_attribute("message.id", str(message.id))
            attachment_span.set_attribute("attachments.count", len(parsed.attachments))
            _save_attachments(message, parsed.attachments)

        owner_id = message.owner_agent_id
        if owner_id:
            # Update last interaction timestamp and reactivate if needed
            agent_obj: PersistentAgent | None = None
            try:
                with transaction.atomic():
                    agent_locked: PersistentAgent = (
                        PersistentAgent.objects.alive().select_for_update()
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
                agent_obj = PersistentAgent.objects.alive().filter(id=owner_id).select_related("user").first()

            if (
                agent_obj is not None
                and agent_obj.user_id
                and channel_val in {CommsChannel.WEB, CommsChannel.EMAIL, CommsChannel.SMS}
            ):
                # Only owner-authored inbound messages should count as user action signals.
                is_owner_authored_message = _is_owner_sender(agent_obj, channel_val, parsed.sender)
                if is_owner_authored_message:
                    marketing_props = Analytics.with_org_properties(
                        {
                            "agent_id": str(agent_obj.id),
                            "channel": channel_val,
                            "message_length": len(parsed.body or ""),
                            "attachments_count": len(parsed.attachments),
                        },
                        organization=getattr(agent_obj, "organization", None),
                    )
                    transaction.on_commit(
                        lambda user=agent_obj.user, marketing_props=marketing_props.copy(): emit_configured_custom_capi_event(
                            user=user,
                            event_name=ConfiguredCustomEvent.INBOUND_MESSAGE,
                            plan_owner=getattr(agent_obj, "organization", None) or getattr(agent_obj, "user", None),
                            properties=marketing_props,
                        )
                    )

            # Before triggering agent processing, check if the agent owner's
            # account is billing-paused. If so, send a one-off auto-reply to the
            # current sender and skip processing for this inbound attempt.
            should_skip_processing = False
            pause_state = {"paused": False, "reason": "", "paused_at": None}
            try:
                if agent_obj and (channel_val in {CommsChannel.EMAIL, CommsChannel.SMS} or is_inbound_webhook):
                    owner = resolve_agent_owner(agent_obj)
                    pause_state = get_owner_execution_pause_state(owner)
                    pause_reason = pause_state["reason"] or ""
                    if pause_state["paused"] and is_billing_execution_pause_reason(pause_reason):
                        should_skip_processing = True
                        if (
                            channel_val in {CommsChannel.EMAIL, CommsChannel.SMS}
                            and parsed.sender
                            and agent_obj.is_sender_whitelisted(channel_val, parsed.sender)
                        ):
                            try:
                                send_billing_pause_auto_reply(
                                    agent_obj,
                                    from_ep,
                                    reason=pause_reason,
                                )
                            except Exception:
                                logging.exception(
                                    "Failed sending billing pause auto-reply for agent %s",
                                    agent_obj.id,
                                )
            except Exception:
                logging.exception("Error during billing-pause pre-processing check")

            # Let the orchestrator decide how to respond; only notify the UI that credits are exhausted.
            try:
                if not should_skip_processing and agent_obj and agent_obj.user_id and channel_val == CommsChannel.WEB:
                    from tasks.services import TaskCreditService

                    if agent_obj.is_sender_whitelisted(CommsChannel.WEB, parsed.sender):
                        owner = getattr(agent_obj, "organization", None) or getattr(agent_obj, "user", None)
                        available = TaskCreditService.calculate_available_tasks_for_owner(owner)
                        min_cost = get_tool_credit_cost_for_channel(channel_val)
                        if available != TASKS_UNLIMITED and available < min_cost:
                            # Send credit_event via websocket to trigger frontend refresh
                            def _send_credit_event():
                                try:
                                    from asgiref.sync import async_to_sync
                                    from channels.layers import get_channel_layer
                                    channel_layer = get_channel_layer()
                                    if channel_layer is not None:
                                        group_name = f"agent-chat-{agent_obj.id}"
                                        payload = {
                                            "kind": "task_credits_exhausted",
                                            "status": "out_of_credits",
                                            "available": float(available),
                                        }
                                        async_to_sync(channel_layer.group_send)(
                                            group_name,
                                            {"type": "credit_event", "agent_id": str(agent_obj.id), "payload": payload},
                                        )
                                except Exception:
                                    logging.debug("Failed to send credit_event for agent %s", agent_obj.id, exc_info=True)
                            transaction.on_commit(_send_credit_event)
            except Exception:
                logging.exception("Error during out-of-credits pre-processing check (WEB)")

            if processing_dispatch is not None:
                processing_dispatch.message_id = str(message.id)
                processing_dispatch.has_attachments = message.attachments.exists()
                processing_dispatch.filespace_import_mode = filespace_import_mode
                processing_dispatch.should_skip_processing = should_skip_processing
                processing_dispatch.persisted_at = message_persisted_at
                processing_dispatch.ready = True
                if not prioritize_processing_dispatch:
                    transaction.on_commit(processing_dispatch.dispatch)

        return InboundMessageInfo(message=message)


@tracer.start_as_current_span("ingest_inbound_webhook_message")
def ingest_inbound_webhook_message(
    webhook: PersistentAgentInboundWebhook,
    *,
    body: str,
    raw_payload: MutableMapping[str, Any],
    attachments: Iterable[Any] = (),
    filespace_import_mode: str = "sync",
) -> InboundMessageInfo:
    """Persist an inbound webhook call as an OTHER-channel message and queue the agent."""

    agent = webhook.agent
    recipient_endpoint = _ensure_agent_inbound_webhook_endpoint(agent)
    sender_address = build_inbound_webhook_sender_address(webhook.id)
    recipient_address = recipient_endpoint.address

    payload = dict(raw_payload or {})
    payload.setdefault("source", "inbound_webhook")
    payload.setdefault("source_kind", "webhook")
    payload.setdefault("source_label", webhook.name)
    payload.setdefault("webhook_id", str(webhook.id))
    payload.setdefault("webhook_name", webhook.name)

    parsed = ParsedMessage(
        sender=sender_address,
        recipient=recipient_address,
        subject=None,
        body=body,
        attachments=list(attachments),
        raw_payload=payload,
        msg_channel=CommsChannel.OTHER.value,
    )
    info = ingest_inbound_message(
        CommsChannel.OTHER,
        parsed,
        filespace_import_mode=filespace_import_mode,
    )

    conversation_id = getattr(info.message, "conversation_id", None)
    if conversation_id:
        PersistentAgentConversation.objects.filter(id=conversation_id).update(display_name=webhook.name)
    webhook.mark_triggered()
    return info

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

    def _trigger_processing() -> None:
        if not trigger_processing:
            return
        from api.agent.tasks import enqueue_interactive_process_agent_events
        inbound_generation = bump_human_inbound_generation(agent.id)
        enqueue_interactive_process_agent_events(
            str(agent.id),
            eval_run_id=eval_run_id,
            inbound_generation=inbound_generation,
        )

    if attachments:
        message_id = str(message.id)
        def _import_then_process() -> None:
            try:
                import_message_attachments_to_filespace(message_id)
            except Exception:
                logging.exception("Failed synchronous filespace import for message %s", message_id)
            _trigger_processing()

        transaction.on_commit(_import_then_process)
    else:
        transaction.on_commit(_trigger_processing)

    return message, conv
