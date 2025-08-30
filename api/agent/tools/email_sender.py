"""
Email sending tool for persistent agents.

This module provides email sending functionality for persistent agents,
including tool definition and execution logic.
"""

import logging
from typing import Dict, Any

from ...models import (
    switch_is_active,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    CommsChannel,
    DeliveryStatus,
)
from ..comms.outbound_delivery import deliver_agent_email

logger = logging.getLogger(__name__)


def get_send_email_tool() -> Dict[str, Any]:
    """Return the send_email tool definition for the LLM."""
    return {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Sends an email to a recipient. Write the body as lightweight, mobile-first HTML (using simple <p>, <br>, <ul>, <ol>, <li>, etc.) that feels like it was typed in a normal email client, not a marketing blast. DO NOT include <html>, <head>, or <body> tags—the system will wrap your content. Avoid markdown formatting and heavy styling. Quote recent parts of the conversation when relevant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_address": {"type": "string", "description": "Recipient email."},
                    "subject": {"type": "string", "description": "Email subject."},
                    "mobile_first_html": {"type": "string", "description": "Email content as lightweight HTML, excluding <html>, <head>, and <body> tags. Links should use html links, e.g. <a href=\"https://news.ycombinator.com\">News</a>"},
                },
                "required": ["to_address", "subject", "mobile_first_html"],
            },
        },
    }


def execute_send_email(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the send_email tool for a persistent agent."""
    to_address, subject, mobile_first_html = params.get("to_address"), params.get("subject"), params.get("mobile_first_html")
    if not all([to_address, subject, mobile_first_html]):
        return {"status": "error", "message": "Missing required parameters: to_address, subject, or mobile_first_html"}

    # Log email attempt
    body_preview = mobile_first_html[:100] + "..." if len(mobile_first_html) > 100 else mobile_first_html
    logger.info(
        "Agent %s sending email to %s, subject: '%s', body: %s",
        agent.id, to_address, subject, body_preview
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
            return {"status": "error", "message": "Agent has no configured email endpoint to send from."}

        if not agent.is_recipient_whitelisted(CommsChannel.EMAIL, to_address):
            return {"status": "error", "message": "Recipient address not allowed for this agent."}

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