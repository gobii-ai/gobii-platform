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
from ..comms.outbound_delivery import deliver_agent_sms
from ...models import (
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage, LinkShortener,
)
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
            "description": "Sends an SMS message to a recipient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_number": {"type": "string", "description": "E.164 phone number."},
                    "body": {"type": "string", "description": "SMS content."},
                },
                "required": ["to_number", "body"],
            },
        },
    }


@tracer.start_as_current_span("SMS Sender - execute_send_sms")
def execute_send_sms(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute SMS sending for a persistent agent."""
    to_number, body = params.get("to_number"), params.get("body")
    if not all([to_number, body]):
        return {"status": "error", "message": "Missing required parameters: to_number or body"}

    if len(body) > settings.SMS_MAX_BODY_LENGTH:
        return {
            "status": "error",
            "message": f"SMS body exceeds maximum length of {settings.SMS_MAX_BODY_LENGTH} characters. Please shorten it, or split it into multiple messages."
        }

    # Log SMS attempt
    body_preview = body[:100] + "..." if len(body) > 100 else body
    logger.info(
        "Agent %s sending SMS to %s, body: %s",
        agent.id, to_number, body_preview
    )

    try:
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

        if not agent.is_recipient_whitelisted(CommsChannel.SMS, to_number):
            return {"status": "error", "message": "Recipient number not allowed for this agent."}

        to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.SMS, address=to_number, defaults={"owner_agent": None}
        )

        # Perform link shortening in body if needed
        body = shorten_links_in_body(body, user=agent.user)

        message = PersistentAgentMessage.objects.create(
            owner_agent=agent,
            from_endpoint=from_endpoint,
            to_endpoint=to_endpoint,
            is_outbound=True,
            body=body,
            raw_payload={},
        )

        deliver_agent_sms(message)

        return {"status": "ok", "message": f"SMS queued for {to_number}."}

    except Exception as e:
        logger.exception("Failed to create PersistentAgentMessage for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to send SMS: {e}"}

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