"""
Email sending tool for persistent agents.

This module provides email sending functionality for persistent agents,
including tool definition and execution logic.
"""

import logging
from typing import Dict, Any

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
from .outbound_duplicate_guard import detect_recent_duplicate_message
from util.integrations import postmark_status
from util.text_sanitizer import decode_unicode_escapes, strip_control_chars
from .agent_variables import substitute_variables_with_filespace
from ..files.attachment_helpers import (
    AttachmentResolutionError,
    create_message_attachments,
    resolve_filespace_attachments,
)
from ..files.filespace_service import broadcast_message_attachment_update
from api.services.email_verification import require_verified_email, EmailVerificationError

logger = logging.getLogger(__name__)


def _should_continue_work(params: Dict[str, Any]) -> bool:
    """Return True if the caller indicated ongoing work after this send."""
    raw = params.get("will_continue_work")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        return normalized in {"1", "true", "yes"}
    return bool(raw)

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
                    "mobile_first_html": {"type": "string", "description": "Email content as lightweight HTML, excluding <html>, <head>, and <body> tags. Use single quotes for attributes, e.g. <a href='https://news.ycombinator.com'>News</a>. Must be actual email content, NOT tool call syntax. XML like <function_calls> or <invoke> does NOT execute tools—it will be sent as literal text."},
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of filespace paths or $[/path] variables from the default filespace. Pass attachments here; do not paste file paths into the email body unless you want them shown as text.",
                    },
                    "will_continue_work": {
                        "type": "boolean",
                        "description": "REQUIRED. true = you'll take another action, false = you're done. Omitting this stops you for good—choose wisely.",
                    },
                },
                "required": ["to_address", "subject", "mobile_first_html", "will_continue_work"],
            },
        },
    }


def execute_send_email(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the send_email tool for a persistent agent."""
    try:
        require_verified_email(agent.user, action_description="send emails")
    except EmailVerificationError as e:
        return e.to_tool_response()

    to_address = params.get("to_address")
    subject = params.get("subject")
    # Decode escape sequences and strip control chars from HTML body
    mobile_first_html = decode_unicode_escapes(params.get("mobile_first_html"))
    mobile_first_html = strip_control_chars(mobile_first_html)
    # Substitute $[var] placeholders with actual values (e.g., $[/charts/...]).
    mobile_first_html = substitute_variables_with_filespace(mobile_first_html, agent)
    cc_addresses = params.get("cc_addresses", [])  # Optional list of CC addresses
    will_continue = _should_continue_work(params)
    attachment_paths = params.get("attachments")

    if not all([to_address, subject, mobile_first_html]):
        return {"status": "error", "message": "Missing required parameters: to_address, subject, or mobile_first_html"}

    try:
        resolved_attachments = resolve_filespace_attachments(agent, attachment_paths)
    except AttachmentResolutionError as exc:
        return {"status": "error", "message": str(exc)}

    # Log email attempt
    body_preview = mobile_first_html[:100] + "..." if len(mobile_first_html) > 100 else mobile_first_html
    cc_info = f", CC: {cc_addresses}" if cc_addresses else ""
    attachment_info = f", attachments: {len(resolved_attachments)}" if resolved_attachments else ""
    logger.info(
        "Agent %s sending email to %s%s%s, subject: '%s', body: %s",
        agent.id, to_address, cc_info, attachment_info, subject, body_preview
    )

    try:
        # Ensure a healthy DB connection for subsequent ORM ops
        from django.db import close_old_connections
        from django.db.utils import OperationalError
        duplicate = detect_recent_duplicate_message(
            agent,
            channel=CommsChannel.EMAIL,
            body=mobile_first_html,
            to_address=to_address,
        )
        if duplicate:
            return duplicate.to_error_response()

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
            postmark_state = postmark_status()
            if simulation_flag and not postmark_state.enabled:
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
            if resolved_attachments:
                create_message_attachments(message, resolved_attachments)
                broadcast_message_attachment_update(str(message.id))
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
            if resolved_attachments:
                create_message_attachments(message, resolved_attachments)
                broadcast_message_attachment_update(str(message.id))

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

        return {
            "status": "ok",
            "message": f"Email sent to {to_address}.",
            "auto_sleep_ok": not will_continue,
        }

    except Exception as e:
        logger.exception("Failed to create and deliver email for agent %s", agent.id)
        return {"status": "error", "message": f"Failed to send email: {e}"} 
