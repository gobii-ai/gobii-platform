"""
Email sending tool for persistent agents.

This module provides email sending functionality for persistent agents,
including tool definition and execution logic.
"""

import logging
from typing import Dict, Any, Optional

from ...models import (
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    CommsChannel,
    DeliveryStatus,
)
from django.conf import settings
import os
from ..comms.outbound_delivery import deliver_agent_email

logger = logging.getLogger(__name__)


def _restore_truncated_unicode(text: Optional[str]) -> Optional[str]:
    """Repair common smart punctuation that had its high byte stripped earlier."""
    if not text:
        return text

    needs_fix = False
    for ch in text:
        code = ord(ch)
        if 0 <= code < 32 and code not in (9, 10, 13):
            needs_fix = True
            break

    if not needs_fix:
        return text

    fixed_chars = []
    for ch in text:
        code = ord(ch)
        if 0 <= code < 32 and code not in (9, 10, 13):
            fixed_chars.append(chr(code + 0x2000))
        else:
            fixed_chars.append(ch)

    repaired = "".join(fixed_chars)
    logger.debug("Repaired truncated unicode punctuation: %s -> %s", text[:50], repaired[:50])
    return repaired


def get_send_email_tool() -> Dict[str, Any]:
    """Return the send_email tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Sends an email to a recipient. Write the body as lightweight, mobile-first HTML (using simple <p>, <br>, <ul>, <ol>, <li>, etc.) that feels like it was typed in a normal email client, not a marketing blast. DO NOT include <html>, <head>, or <body> tags—the system will wrap your content. Avoid markdown formatting and heavy styling. Quote recent parts of the conversation when relevant. "
                "IMPORTANT: Use single quotes for ALL HTML attributes (e.g., <a href='https://example.com'>link</a>) to keep the JSON arguments valid. Do NOT use double quotes in HTML attributes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_address": {"type": "string", "description": "Recipient email."},
                    "cc_addresses": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "format": "email",
                        },
                        "description": "List of CC email addresses (optional)"
                    },
                    "subject": {"type": "string", "description": "Email subject."},
                    "mobile_first_html": {"type": "string", "description": "Email content as lightweight HTML, excluding <html>, <head>, and <body> tags. Use single quotes for attributes, e.g. <a href='https://news.ycombinator.com'>News</a>"},
                },
                "required": ["to_address", "subject", "mobile_first_html"],
            },
        },
    }


def execute_send_email(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the send_email tool for a persistent agent."""
    to_address = params.get("to_address")
    subject = _restore_truncated_unicode(params.get("subject"))
    mobile_first_html = _restore_truncated_unicode(params.get("mobile_first_html"))
    cc_addresses = params.get("cc_addresses", [])  # Optional list of CC addresses
    
    if not all([to_address, subject, mobile_first_html]):
        return {"status": "error", "message": "Missing required parameters: to_address, subject, or mobile_first_html"}

    # Log email attempt
    body_preview = mobile_first_html[:100] + "..." if len(mobile_first_html) > 100 else mobile_first_html
    cc_info = f", CC: {cc_addresses}" if cc_addresses else ""
    logger.info(
        "Agent %s sending email to %s%s, subject: '%s', body: %s",
        agent.id, to_address, cc_info, subject, body_preview
    )

    try:
        # Ensure a healthy DB connection for subsequent ORM ops
        from django.db import close_old_connections
        from django.db.utils import OperationalError
        close_old_connections()

        from_endpoint = (
            PersistentAgentCommsEndpoint.objects.filter(
                owner_agent=agent, channel=CommsChannel.EMAIL, is_primary=True
            ).first()
            or PersistentAgentCommsEndpoint.objects.filter(
                owner_agent=agent, channel=CommsChannel.EMAIL
            ).first()
        )
        if not from_endpoint:
            # In local/dev, if simulation is enabled and no Postmark token is configured,
            # auto-provision a temporary agent-owned email endpoint so we can simulate
            # delivery and persist the outbound message for history/UX.
            simulation_flag = getattr(settings, "SIMULATE_EMAIL_DELIVERY", False)
            postmark_token = os.getenv("POSTMARK_SERVER_TOKEN")
            if simulation_flag and not postmark_token:
                try:
                    # Create a simple local-from address for simulation purposes
                    sim_address = f"agent-{agent.id}@localhost"
                    from_endpoint = PersistentAgentCommsEndpoint.objects.create(
                        owner_agent=agent,
                        channel=CommsChannel.EMAIL,
                        address=sim_address,
                        is_primary=True,
                    )
                    logger.info(
                        "Provisioned simulated from_endpoint %s for agent %s to enable local email simulation",
                        sim_address,
                        agent.id,
                    )
                except Exception as e:
                    logger.exception(
                        "Failed to provision simulated email endpoint for agent %s: %s",
                        agent.id,
                        e,
                    )
                    return {"status": "error", "message": "Agent has no configured email endpoint to send from."}
            else:
                return {"status": "error", "message": "Agent has no configured email endpoint to send from."}

        if not agent.is_recipient_whitelisted(CommsChannel.EMAIL, to_address):
            return {"status": "error", "message": "Recipient address not allowed for this agent."}
        
        # Check whitelist for CC addresses
        for cc_addr in cc_addresses:
            if not agent.is_recipient_whitelisted(CommsChannel.EMAIL, cc_addr):
                return {"status": "error", "message": f"CC address {cc_addr} not allowed for this agent."}

        close_old_connections()
        try:
            to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.EMAIL, address=to_address, defaults={"owner_agent": None}
            )
        except OperationalError:
            close_old_connections()
            to_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.EMAIL, address=to_address, defaults={"owner_agent": None}
            )
        
        # Create CC endpoints
        cc_endpoint_objects = []
        for cc_addr in cc_addresses:
            close_old_connections()
            try:
                cc_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                    channel=CommsChannel.EMAIL, address=cc_addr, defaults={"owner_agent": None}
                )
                cc_endpoint_objects.append(cc_endpoint)
            except OperationalError:
                close_old_connections()
                cc_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                    channel=CommsChannel.EMAIL, address=cc_addr, defaults={"owner_agent": None}
                )
                cc_endpoint_objects.append(cc_endpoint)

        close_old_connections()
        try:
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                to_endpoint=to_endpoint,
                is_outbound=True,
                body=mobile_first_html,
                raw_payload={"subject": subject},
            )
            # Add CC endpoints to the message
            if cc_endpoint_objects:
                message.cc_endpoints.set(cc_endpoint_objects)
        except OperationalError:
            close_old_connections()
            message = PersistentAgentMessage.objects.create(
                owner_agent=agent,
                from_endpoint=from_endpoint,
                to_endpoint=to_endpoint,
                is_outbound=True,
                body=mobile_first_html,
                raw_payload={"subject": subject},
            )
            # Add CC endpoints to the message
            if cc_endpoint_objects:
                message.cc_endpoints.set(cc_endpoint_objects)

        # Immediately attempt delivery
        deliver_agent_email(message)

        # Check the result
        close_old_connections()
        try:
            message.refresh_from_db()
        except OperationalError:
            close_old_connections()
            message.refresh_from_db()
        if message.latest_status == DeliveryStatus.FAILED:
            return {"status": "error", "message": f"Email failed to send: {message.latest_error_message}"}

        return {"status": "ok", "message": f"Email sent to {to_address}."}

    except Exception as e:
        logger.exception("Failed to create and deliver email for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to send email: {e}"} 
