"""
SMS sender tool for persistent agents.

This module provides SMS sending functionality for persistent agents,
including tool definition and execution logic.
"""
import logging

from typing import Dict, Any, List, Tuple
import re
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.urls.base import reverse
from django.conf import settings

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from ..comms.outbound_delivery import deliver_agent_sms, deliver_agent_group_sms
from .outbound_duplicate_guard import detect_recent_duplicate_message
from util.text_sanitizer import strip_control_chars
from ..comms.message_service import _get_or_create_conversation, _ensure_participant
from ...models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentMessage,
    PersistentAgentSmsGroup,
    PersistentAgentSmsEndpoint,
    LinkShortener,
)
from util import sms
from opentelemetry import trace
from urlextract import URLExtract

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')

def get_send_sms_tool() -> Dict[str, Any]:
    """Return the SMS tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "send_sms",
            "description": "Sends an SMS message to a single recipient or a saved group conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_number": {
                        "type": "string",
                        "description": "Primary E.164 phone number for a 1:1 message.",
                    },
                    "group_id": {
                        "type": "string",
                        "description": "Identifier of a saved SMS group configured for this agent.",
                    },
                    "body": {"type": "string", "description": "SMS or MMS content."},
                },
                "required": ["body"],
            },
        },
    }


@tracer.start_as_current_span("SMS Sender - execute_send_sms")
def execute_send_sms(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute SMS sending for a persistent agent."""
    body = strip_control_chars(params.get("body"))
    to_number = params.get("to_number")
    group_id = params.get("group_id")

    if not body:
        return {"status": "error", "message": "Missing required parameter: body"}

    if group_id and to_number:
        return {"status": "error", "message": "Provide either to_number for 1:1 SMS or group_id for a group message, not both."}

    if len(body) > settings.SMS_MAX_BODY_LENGTH:
        return {
            "status": "error",
            "message": f"SMS body exceeds maximum length of {settings.SMS_MAX_BODY_LENGTH} characters. Please shorten it, or split it into multiple messages.",
        }

    from_endpoint = (
        PersistentAgentCommsEndpoint.objects.filter(
            owner_agent=agent, channel=CommsChannel.SMS, is_primary=True
        ).first()
        or PersistentAgentCommsEndpoint.objects.filter(
            owner_agent=agent, channel=CommsChannel.SMS
        ).first()
    )
    if not from_endpoint:
        return {"status": "error", "message": "Agent has no configured SMS endpoint to send from."}

    logger.info(
        "Agent %s preparing SMS via endpoint %s (group_id=%s, to=%s)",
        agent.id,
        from_endpoint.address,
        group_id,
        to_number,
    )

    try:
        if group_id:
            return _send_group_sms(agent, from_endpoint, body, group_id)
        if not to_number:
            return {
                "status": "error",
                "message": "Provide a to_number for 1:1 SMS or use the sms_group_id provided in your context for group texting.",
            }
        return _send_single_sms(agent, from_endpoint, body, to_number)
    except Exception as exc:  # pragma: no cover - safety net
        logger.exception("Failed sending SMS for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to send SMS: {exc}"}


def _send_single_sms(
    agent: PersistentAgent,
    from_endpoint: PersistentAgentCommsEndpoint,
    body: str,
    to_number: str,
) -> Dict[str, Any]:
    if not to_number:
        return {"status": "error", "message": "to_number is required for single-recipient SMS."}

    if not agent.is_recipient_whitelisted(CommsChannel.SMS, to_number):
        return {"status": "error", "message": "Recipient number not allowed for this agent."}

    to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
        channel=CommsChannel.SMS,
        address=to_number,
        defaults={"owner_agent": None},
    )

    body = shorten_links_in_body(body, user=agent.user)

    duplicate = detect_recent_duplicate_message(
        agent,
        channel=CommsChannel.SMS,
        body=body,
        to_address=to_number,
    )
    if duplicate:
        return duplicate.to_error_response()

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=from_endpoint,
        to_endpoint=to_endpoint,
        is_outbound=True,
        body=body,
        raw_payload={},
    )

    deliver_agent_sms(message)

    return {
        "status": "ok",
        "message": f"SMS queued for {to_number}.",
        "auto_sleep_ok": True,
    }

def _send_group_sms(
    agent: PersistentAgent,
    from_endpoint: PersistentAgentCommsEndpoint,
    body: str,
    group_id: str,
) -> Dict[str, Any]:
    try:
        agent.sync_sms_allowlist_group()
    except Exception:
        logger.exception("Failed to refresh allowlist group for agent %s", agent.id)

    try:
        group = (
            PersistentAgentSmsGroup.objects.prefetch_related("members")
            .get(id=group_id, agent=agent, is_active=True)
        )
    except PersistentAgentSmsGroup.DoesNotExist:
        return {"status": "error", "message": "Group not found or inactive for this agent."}

    members = list(group.members.order_by("phone_number"))
    if not members:
        return {"status": "error", "message": "Group has no participants to message."}
    max_members = PersistentAgentSmsGroup.MAX_MEMBERS
    if len(members) > max_members:
        return {
            "status": "error",
            "message": (
                f"Group texting supports at most {max_members} saved recipients "
                "(10 total including you and the Gobii agent)."
            ),
        }

    # Ensure every participant is whitelisted for outbound SMS
    for member in members:
        if not agent.is_recipient_whitelisted(CommsChannel.SMS, member.phone_number):
            return {
                "status": "error",
                "message": f"Group member {member.phone_number} is not allowed for this agent.",
            }

    # Ensure proxy endpoint supports MMS (required for group messaging)
    try:
        sms_meta = from_endpoint.sms_meta
    except AttributeError:
        sms_meta = None
    except PersistentAgentSmsEndpoint.DoesNotExist:
        sms_meta = None
    if not sms_meta or not getattr(sms_meta, "supports_mms", False):
        return {
            "status": "error",
            "message": "Agent's SMS number must support MMS to send group texts.",
        }

    body = shorten_links_in_body(body, user=agent.user)

    duplicate = detect_recent_duplicate_message(
        agent,
        channel=CommsChannel.SMS,
        body=body,
        to_address=str(group.id),
    )
    if duplicate:
        return duplicate.to_error_response()

    # Ensure the Twilio conversation exists and is up to date
    try:
        conversation_sid = sms.ensure_group_conversation(group, proxy_number=from_endpoint.address)
    except Exception as exc:
        logger.exception("Failed to ensure Twilio conversation for group %s", group.id)
        return {"status": "error", "message": f"Unable to prepare group conversation: {exc}"}

    conversation = _get_or_create_conversation(
        CommsChannel.SMS,
        conversation_sid,
        owner_agent=agent,
        sms_group=group,
        display_name=group.name,
    )

    # Ensure participants are tracked for the conversation timeline
    _ensure_participant(
        conversation,
        from_endpoint,
        PersistentAgentConversationParticipant.ParticipantRole.AGENT,
    )

    member_endpoints: list[PersistentAgentCommsEndpoint] = []
    for member in members:
        endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.SMS,
            address=member.phone_number,
            defaults={"owner_agent": None},
        )
        member_endpoints.append(endpoint)
        _ensure_participant(
            conversation,
            endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )

    message = PersistentAgentMessage.objects.create(
        owner_agent=agent,
        from_endpoint=from_endpoint,
        conversation=conversation,
        is_outbound=True,
        body=body,
        raw_payload={
            "sms_group_id": str(group.id),
            "sms_group_name": group.name,
            "participant_numbers": [member.phone_number for member in members],
        },
    )

    deliver_agent_group_sms(message, group)

    participant_list = ", ".join(m.phone_number for m in members)
    return {
        "status": "ok",
        "message": f"Group SMS queued to {len(members)} participants: {participant_list}.",
        "auto_sleep_ok": True,
    }

@tracer.start_as_current_span("SMS Sender - shorten_links_in_body")
def shorten_links_in_body(body: str, user: User | None = None) -> str:
    """
    Replace every HTTP/HTTPS URL in *body* with a per‑message short link.

    The function is idempotent: already‑shortened links are skipped,
    and every long URL is shortened exactly once per call.
    """
    extractor = URLExtract()
    current_site = Site.objects.get_current()
    protocol = "https://"                       # your outbound scheme
    base = f"{protocol}{current_site.domain}"

    # 1️⃣  Find every URL (with indices so we can replace safely later).
    matches: List[Tuple[str, Tuple[int, int]]] = list(
        extractor.gen_urls(body, get_indices=True)
    )

    if not matches:
        return body

    # 2️⃣  Build / fetch one short URL for each *distinct* long URL.
    mapping: Dict[str, str] = {}
    for url, _ in matches:
        if url in mapping:
            continue

        # If the link ends in . remove it; issue with extraction in sentences
        if url.endswith('.'):
            url = url[:-1]

        short_obj = create_shortened_link(url, user)
        rel = reverse("short_link", kwargs={"code": short_obj.code})
        mapping[url] = f"{base}{rel}"

    # 3️⃣  Replace URLs in a *single* pass, using a compiled alternation.
    #     Longer URLs first so we do not match 'http://a.com' inside 'http://a.com/x'.
    pattern = re.compile(r"(" + "|".join(map(re.escape, sorted(mapping, key=len, reverse=True))) + r")")
    return pattern.sub(lambda m: mapping[m.group(0)], body)

@tracer.start_as_current_span("SMS Sender - create_shortened_link")
def create_shortened_link(link: str, user: User | None = None) -> LinkShortener:
    """
    Create a shortened link using the LinkShortener service.

    This function is used to create a shortened version of a given link.
    It returns the shortened URL.
    """
    link = ensure_scheme(link)

    shortened = LinkShortener(
        url=link,
        user=user
    )
    shortened.save()

    rel = reverse('short_link', kwargs={'code': shortened.code})
    protocol = 'https://'

    # Ensure the site domain is used to create the absolute URL
    current_site = Site.objects.get_current()
    url = f"{protocol}{current_site.domain}{rel}"

    properties = {
        "link_original_url": link,
        "link_shortened_url": url,
        "link_code": shortened.code,
    }

    if user:
        properties["user_id"] = user.id
        properties["user_username"] = user.username

        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.SMS_SHORTENED_LINK_CREATED,
            source=AnalyticsSource.SMS,
            properties=properties
        )

    return shortened


def ensure_scheme(url: str, default="https") -> str:
    """
    Return a fully-qualified URL.
    • Adds `https://` (or your chosen default) if the scheme is missing.
    • Leaves protocol-relative URLs (`//example.com`) alone except for
      attaching the default scheme in front.
    """
    p = urlparse(url)

    if p.scheme:
        return url

    # www.example.com → //www.example.com
    if not p.netloc and "." in p.path and " " not in p.path:
        return f"{default}://{url.lstrip('/')}"

    if url.startswith("//"):
        return f"{default}:{url}"

    return f"{default}://{url}"
