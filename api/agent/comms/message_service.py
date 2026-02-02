from __future__ import annotations

from uuid import UUID
from urllib.parse import urlparse

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.tool_costs import get_tool_credit_cost_for_channel

"""Service helpers for inbound communication messages."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Any, Tuple

import logging
import mimetypes
import os
import requests
from django.contrib.sites.models import Site
from django.core.exceptions import MultipleObjectsReturned
from django.core.files.base import ContentFile, File
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.templatetags.static import static
from django.utils import timezone
from ..files.filespace_service import enqueue_import_after_commit, import_message_attachments_to_filespace

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
from api.services.system_settings import get_max_file_size

from .adapters import ParsedMessage
from .attachment_filters import is_signature_image_attachment
from .outbound_delivery import deliver_agent_email, deliver_agent_sms
from observability import traced
from opentelemetry import baggage
from config import settings
from util.constants.task_constants import TASKS_UNLIMITED
from opentelemetry import trace
from constants.plans import PlanNamesChoices
from util.subscription_helper import get_owner_plan
from util.urls import (
    append_context_query,
    append_query_params,
    build_daily_limit_action_token,
)

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

@tracer.start_as_current_span("_save_attachments")
def _save_attachments(message: PersistentAgentMessage, attachments: Iterable[Any]) -> None:
    max_bytes = get_max_file_size()
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
                    logging.warning(f"File '{filename} exceeds max size of {max_bytes} bytes, skipping.")
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
                            logging.warning(f"File '{filename} exceeds max size of {max_bytes} bytes, skipping.")
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
                    logging.warning(f"File '{filename} exceeds max size of {max_bytes} bytes, skipping.")
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

def _build_site_url(path: str) -> str:
    """Return an absolute URL for a site-relative path."""
    from django.conf import settings as django_settings

    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base_url = (getattr(django_settings, "PUBLIC_SITE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        current_site = Site.objects.get_current()
        protocol = "https://"
        base_url = f"{protocol}{current_site.domain}"
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{normalized}"

@tracer.start_as_current_span("_build_agent_detail_url")
def _build_agent_detail_url(agent) -> str:
    """Return an absolute URL to the agent's detail page."""
    path = reverse("agent_detail", kwargs={"pk": agent.id})
    return _build_site_url(path)

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
    """Send a daily credit limit notice to the inbound sender for SMS/web; email notifies owner."""

    channel_value = channel.value if isinstance(channel, CommsChannel) else channel
    if channel_value == CommsChannel.EMAIL.value:
        return send_owner_daily_credit_hard_limit_notice(agent)

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
        "I reached my daily task limit and am not able to continue today. "
        f"Adjust the limit here: {link}"
    )
    analytics_source = {
        CommsChannel.EMAIL.value: AnalyticsSource.EMAIL,
        CommsChannel.SMS.value: AnalyticsSource.SMS,
        CommsChannel.WEB.value: AnalyticsSource.WEB,
    }.get(str(channel_value), AnalyticsSource.AGENT)

    try:
        if channel_value == CommsChannel.SMS.value:
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
                        "channel": channel_value,
                        "recipient": parsed.sender,
                        "plan_id": plan_id,
                        "plan_label": plan_label,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.UPSELL_MESSAGE_SHOWN,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "message_type": "daily_hard_limit",
                        "medium": "sms",
                        "recipient_type": "inbound_contact",
                        "upsell_shown": True,
                        "plan": plan_id,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            return True

        if channel_value == CommsChannel.WEB.value:
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
                        "channel": channel_value,
                        "recipient": parsed.sender,
                        "plan_id": plan_id,
                        "plan_label": plan_label,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.UPSELL_MESSAGE_SHOWN,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "message_type": "daily_hard_limit",
                        "medium": "web_chat_message",
                        "recipient_type": "inbound_contact",
                        "upsell_shown": True,
                        "plan": plan_id,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            return True

    except Exception:
        logging.exception("Failed sending daily credit limit notice for agent %s", agent.id)

    return False


@tracer.start_as_current_span("send_owner_daily_credit_hard_limit_notice")
def send_owner_daily_credit_hard_limit_notice(agent: PersistentAgent) -> bool:
    """Notify the agent owner that the daily hard limit has been reached."""
    try:
        now = timezone.now()
        last_notice = getattr(agent, "daily_credit_hard_limit_notice_at", None)
        if last_notice and timezone.localdate(last_notice) == timezone.localdate(now):
            return False

        endpoint = getattr(agent, "preferred_contact_endpoint", None)
        if not endpoint:
            logging.info(
                "Agent %s has no preferred contact endpoint; skipping hard limit notice.",
                agent.id,
            )
            return False

        link = append_context_query(_build_agent_detail_url(agent), agent.organization_id)
        owner = agent.organization or agent.user
        plan = get_owner_plan(owner) if owner is not None else None
        plan_id = str(plan.get("id", "")).lower() if plan else ""
        is_free_plan = plan_id == PlanNamesChoices.FREE.value
        upgrade_url = None
        task_pack_url = None
        if is_free_plan and settings.GOBII_PROPRIETARY_MODE:
            try:
                upgrade_url = _build_site_url(reverse("proprietary:pricing"))
            except NoReverseMatch:
                upgrade_url = None
        elif settings.GOBII_PROPRIETARY_MODE:
            try:
                billing_url = _build_site_url(reverse("billing"))
            except NoReverseMatch:
                billing_url = None
            else:
                task_pack_url = append_context_query(billing_url, agent.organization_id)
        subject = f"{agent.name} reached today's task limit"
        text_body = (
            "I reached my daily task limit and am not able to continue today. "
            f"Adjust the limit here: {link}"
        )
        try:
            double_limit_url = _build_site_url(
                reverse(
                    "agent_daily_limit_action",
                    kwargs={"pk": agent.id, "action": "double"},
                )
            )
            unlimited_limit_url = _build_site_url(
                reverse(
                    "agent_daily_limit_action",
                    kwargs={"pk": agent.id, "action": "unlimited"},
                )
            )
        except NoReverseMatch:
            double_limit_url = link
            unlimited_limit_url = link
        else:
            double_limit_url = append_query_params(
                double_limit_url,
                {"token": build_daily_limit_action_token(str(agent.id), "double")},
            )
            unlimited_limit_url = append_query_params(
                unlimited_limit_url,
                {"token": build_daily_limit_action_token(str(agent.id), "unlimited")},
            )
            if agent.organization_id:
                double_limit_url = append_context_query(double_limit_url, agent.organization_id)
                unlimited_limit_url = append_context_query(unlimited_limit_url, agent.organization_id)

        try:
            logo_url = _build_site_url(static("images/noBgBlue.png"))
        except (Site.DoesNotExist, MultipleObjectsReturned, DatabaseError, ValueError) as exc:
            logging.warning("Failed to build logo URL for daily credit email: %s", exc)
            logo_url = ""

        email_context = {
            "agent": agent,
            "settings_url": link,
            "double_limit_url": double_limit_url,
            "unlimited_limit_url": unlimited_limit_url,
            "upgrade_url": upgrade_url,
            "task_pack_url": task_pack_url,
            "logo_url": logo_url,
        }
        email_body = render_to_string(
            "emails/agent_daily_credit_owner_notice.html",
            email_context,
        )

        channel_value = endpoint.channel
        analytics_source = {
            CommsChannel.EMAIL: AnalyticsSource.EMAIL,
            CommsChannel.SMS: AnalyticsSource.SMS,
            CommsChannel.WEB: AnalyticsSource.WEB,
        }.get(channel_value, AnalyticsSource.AGENT)

        if channel_value == CommsChannel.EMAIL:
            from_endpoint = _find_agent_endpoint(agent, CommsChannel.EMAIL)
            if not from_endpoint:
                logging.info("Agent %s has no email endpoint for hard limit notice.", agent.id)
                return False
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                to_endpoint=endpoint,
                is_outbound=True,
                body=email_body,
                raw_payload={
                    "subject": subject,
                    "kind": "daily_credit_hard_limit_owner_notice",
                    "hide_in_chat": True,
                },
            )
            deliver_agent_email(message)
        elif channel_value == CommsChannel.SMS:
            from_endpoint = _find_agent_endpoint(agent, CommsChannel.SMS)
            if not from_endpoint:
                logging.info("Agent %s has no SMS endpoint for hard limit notice.", agent.id)
                return False
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                to_endpoint=endpoint,
                is_outbound=True,
                body=text_body,
                raw_payload={"kind": "daily_credit_hard_limit_owner_notice"},
            )
            deliver_agent_sms(message)
        elif channel_value == CommsChannel.WEB:
            agent_endpoint = _ensure_agent_web_endpoint(agent)
            conv = _get_or_create_conversation(
                CommsChannel.WEB.value,
                endpoint.address,
                owner_agent=agent,
            )
            _ensure_participant(
                conv,
                agent_endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.AGENT,
            )
            _ensure_participant(
                conv,
                endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.HUMAN_USER,
            )
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=agent_endpoint,
                conversation=conv,
                is_outbound=True,
                body=text_body,
                raw_payload={"source": "daily_credit_hard_limit_owner_notice"},
            )
            now = timezone.now()
            PersistentAgentMessage.objects.filter(pk=message.pk).update(
                latest_status=DeliveryStatus.DELIVERED,
                latest_sent_at=now,
                latest_delivered_at=now,
                latest_error_code="",
                latest_error_message="",
            )
        else:
            logging.info(
                "Agent %s preferred endpoint channel %s not supported for hard limit notice.",
                agent.id,
                channel_value,
            )
            return False

        try:
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.PERSISTENT_AGENT_DAILY_CREDIT_NOTICE_SENT,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "channel": str(channel_value),
                        "recipient": endpoint.address,
                        "notice_type": "owner_hard_limit",
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
            # Determine medium based on channel
            medium_map = {
                CommsChannel.EMAIL: "email",
                CommsChannel.SMS: "sms",
                CommsChannel.WEB: "web_chat_message",
            }
            medium = medium_map.get(channel_value, "email")
            Analytics.track_event(
                user_id=str(getattr(agent.user, "id", "")),
                event=AnalyticsEvent.UPSELL_MESSAGE_SHOWN,
                source=analytics_source,
                properties=Analytics.with_org_properties(
                    {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "message_type": "daily_hard_limit",
                        "medium": medium,
                        "recipient_type": "owner",
                        "upsell_shown": bool(upgrade_url or task_pack_url),
                        "plan": plan_id,
                    },
                    organization=getattr(agent, "organization", None),
                ),
            )
        except Exception:
            logging.exception(
                "Failed to emit analytics for owner hard limit notice (agent %s)",
                agent.id,
            )

        agent.daily_credit_hard_limit_notice_at = now
        agent.save(update_fields=["daily_credit_hard_limit_notice_at"])
        return True
    except Exception:
        logging.exception(
            "Failed sending owner hard limit notice for agent %s",
            getattr(agent, "id", None),
        )
        return False


@transaction.atomic
@tracer.start_as_current_span("ingest_inbound_message")
def ingest_inbound_message(
    channel: CommsChannel | str,
    parsed: ParsedMessage,
    filespace_import_mode: str = "sync",
) -> InboundMessageInfo:
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
                        owner = getattr(agent_obj, "organization", None) or getattr(agent_obj, "user", None)
                        available = TaskCreditService.calculate_available_tasks_for_owner(owner)
                        min_cost = get_tool_credit_cost_for_channel(channel_val)
                        if available != TASKS_UNLIMITED and available < min_cost:
                            # Prepare and send out-of-credits reply via configured backend (Mailgun in prod)
                            try:
                                context = {
                                    "agent": agent_obj,
                                    "owner": agent_obj.user,
                                    "sender": parsed.sender,
                                    "subject": parsed.subject or "",
                                    "is_proprietary_mode": settings.GOBII_PROPRIETARY_MODE,
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
                                    if agent_obj.organization_id:
                                        from api.models import OrganizationMembership

                                        owner_memberships = OrganizationMembership.objects.filter(
                                            org=agent_obj.organization,
                                            role=OrganizationMembership.OrgRole.OWNER,
                                            status=OrganizationMembership.OrgStatus.ACTIVE,
                                        ).select_related("user")
                                        for membership in owner_memberships:
                                            owner_email = (membership.user.email or "").strip()
                                            if owner_email:
                                                recipients.add(owner_email)
                                    else:
                                        owner_email = (agent_obj.user.email or "").strip()
                                        if owner_email:
                                            recipients.add(owner_email)
                                except Exception:
                                    logging.warning(
                                        "Failed to add owner emails to recipients for agent %s",
                                        agent_obj.id,
                                        exc_info=True,
                                    )

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
                                # Track upsell message shown for out-of-credits email
                                try:
                                    ooc_owner = getattr(agent_obj, "organization", None) or getattr(agent_obj, "user", None)
                                    ooc_plan = get_owner_plan(ooc_owner) if ooc_owner else None
                                    ooc_plan_id = str(ooc_plan.get("id", "")).strip() if ooc_plan else ""
                                except Exception:
                                    ooc_plan_id = ""
                                Analytics.track_event(
                                    user_id=str(getattr(agent_obj.user, "id", "")),

                                    event=AnalyticsEvent.UPSELL_MESSAGE_SHOWN,
                                    source=AnalyticsSource.EMAIL,
                                    properties=Analytics.with_org_properties(
                                        {
                                            "agent_id": str(agent_obj.id),
                                            "agent_name": agent_obj.name,
                                            "message_type": "task_credits_exhausted",
                                            "medium": "email",
                                            "recipient_type": "inbound_contact",
                                            "upsell_shown": True,
                                            "plan": ooc_plan_id,
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

            # Check for out-of-credits on WEB channel (similar to EMAIL check above)
            try:
                if not should_skip_processing and agent_obj and agent_obj.user_id and channel_val == CommsChannel.WEB:
                    from tasks.services import TaskCreditService

                    if agent_obj.is_sender_whitelisted(CommsChannel.WEB, parsed.sender):
                        owner = getattr(agent_obj, "organization", None) or getattr(agent_obj, "user", None)
                        available = TaskCreditService.calculate_available_tasks_for_owner(owner)
                        min_cost = get_tool_credit_cost_for_channel(channel_val)
                        if available != TASKS_UNLIMITED and available < min_cost:
                            should_skip_processing = True
                            try:
                                link = _build_agent_detail_url(agent_obj)
                            except Exception:
                                logging.exception(
                                    "Failed building agent detail URL for agent %s",
                                    agent_obj.id,
                                )
                                link = ""
                            _send_daily_credit_notice(
                                agent_obj,
                                channel_val,
                                parsed,
                                sender_endpoint=from_ep,
                                conversation=conv,
                                link=link,
                            )
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
                                            {"type": "credit_event", "payload": payload},
                                        )
                                except Exception:
                                    logging.debug("Failed to send credit_event for agent %s", agent_obj.id, exc_info=True)
                            transaction.on_commit(_send_credit_event)
            except Exception:
                logging.exception("Error during out-of-credits pre-processing check (WEB)")

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

            def _trigger_processing() -> None:
                if should_skip_processing:
                    return
                from api.agent.tasks import process_agent_events_task
                # Top-level trigger: no budget context provided
                process_agent_events_task.delay(str(owner_id))

            has_attachments = message.attachments.exists()
            message_id = str(message.id)

            if has_attachments and filespace_import_mode == "sync":
                def _import_then_maybe_process() -> None:
                    try:
                        import_message_attachments_to_filespace(message_id)
                    except Exception:
                        logging.exception(
                            "Failed synchronous filespace import for message %s",
                            message_id,
                        )
                    _trigger_processing()

                transaction.on_commit(_import_then_maybe_process)
            else:
                if has_attachments:
                    enqueue_import_after_commit(message_id)
                if not should_skip_processing:
                    transaction.on_commit(_trigger_processing)

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

    def _trigger_processing() -> None:
        if not trigger_processing:
            return
        from api.agent.tasks import process_agent_events_task
        process_agent_events_task.delay(str(agent.id), eval_run_id=eval_run_id)

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
