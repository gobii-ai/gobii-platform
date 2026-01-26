"""
Request contact permission tool for persistent agents.

This tool allows agents to request permission to add people to
their allowlist. The agent owner must approve these requests
before inbound messages from those contacts are accepted.
"""
import logging
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from ...models import (
    PersistentAgent, 
    CommsAllowlistEntry, 
    CommsAllowlistRequest,
    CommsChannel
)

logger = logging.getLogger(__name__)


def get_request_contact_permission_tool() -> dict:
    """Return the tool definition for requesting contact permission."""
    return {
        "type": "function",
        "function": {
            "name": "request_contact_permission",
            "description": (
                "Request permission to add someone to your allowlist. "
                "Creates a request that the user must approve before inbound messages are accepted. "
                "Returns a URL that you MUST send to the user so they can approve the contact. "
                "Check if contact already exists before requesting."
                "Only use an email or phone number the user has previously provided to you, or that is publicly available."
                "Do not guess or fabricate contact details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contacts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "channel": {
                                    "type": "string", 
                                    "enum": ["email", "sms"],
                                    "description": "Communication channel to use"
                                },
                                "address": {
                                    "type": "string", 
                                    "description": "Email address or phone number (E.164 format for SMS)"
                                },
                                "name": {
                                    "type": "string", 
                                    "description": "Contact's name if known (optional)"
                                },
                                "reason": {
                                    "type": "string", 
                                    "description": "Detailed explanation of why you need to contact this person"
                                },
                                "purpose": {
                                    "type": "string", 
                                    "description": "Brief purpose (e.g., 'Schedule meeting', 'Get approval', 'Send report')"
                                }
                            },
                            "required": ["channel", "address", "reason", "purpose"]
                        },
                        "description": "List of contacts to request permission for"
                    }
                },
                "required": ["contacts"]
            }
        }
    }


def execute_request_contact_permission(agent: PersistentAgent, params: dict) -> dict:
    """Create contact permission requests for the agent.
    
    This tool allows agents to request permission to add people to their allowlist.
    The requests are created as CommsAllowlistRequest records that the user must approve.
    """
    contacts = params.get("contacts")
    if not contacts or not isinstance(contacts, list):
        return {"status": "error", "message": "Missing or invalid required parameter: contacts"}
    
    if not contacts:
        return {"status": "error", "message": "At least one contact must be specified"}
    
    created_requests = []
    already_allowed = []
    already_pending = []
    errors = []
    
    logger.info(
        "Agent %s requesting permission for %d contacts",
        agent.id, len(contacts)
    )
    
    for contact in contacts:
        try:
            # Validate required fields
            channel = contact.get("channel")
            address = contact.get("address")
            name = contact.get("name", "")
            reason = contact.get("reason")
            purpose = contact.get("purpose")
            
            if not all([channel, address, reason, purpose]):
                errors.append(f"Missing required fields for contact: {contact}")
                continue
            
            # Validate channel
            try:
                channel_enum = CommsChannel(channel)
            except ValueError:
                errors.append(f"Invalid channel '{channel}'. Must be 'email' or 'sms'")
                continue
            
            # Normalize address
            if channel_enum == CommsChannel.EMAIL:
                address = address.strip().lower()
            else:
                address = address.strip()
            
            # Check if contact already exists in allowlist
            existing_entry = CommsAllowlistEntry.objects.filter(
                agent=agent,
                channel=channel_enum,
                address=address,
                is_active=True
            ).first()
            
            if existing_entry:
                already_allowed.append({
                    "address": address,
                    "channel": channel
                })
                logger.info(
                    "Contact %s (%s) already in allowlist for agent %s",
                    address, channel, agent.id
                )
                continue
            
            # Check if request already pending
            existing_request = CommsAllowlistRequest.objects.filter(
                agent=agent,
                channel=channel_enum,
                address=address,
                status=CommsAllowlistRequest.RequestStatus.PENDING
            ).first()
            
            if existing_request:
                already_pending.append({
                    "address": address,
                    "channel": channel
                })
                logger.info(
                    "Request for %s (%s) already pending for agent %s",
                    address, channel, agent.id
                )
                continue
            
            # Create the contact request
            # Set expiry to 7 days from now by default
            expires_at = timezone.now() + timedelta(days=7)
            
            request = CommsAllowlistRequest.objects.create(
                agent=agent,
                channel=channel_enum,
                address=address,
                name=name,
                reason=reason,
                purpose=purpose,
                expires_at=expires_at
            )
            
            created_requests.append({
                "address": address,
                "channel": channel,
                "name": name or "Unknown",
                "purpose": purpose
            })
            
            logger.info(
                "Created contact request for agent %s: %s (%s) - %s",
                agent.id, address, channel, purpose
            )
            
        except Exception as e:
            error_msg = f"Failed to create request for '{contact.get('address', 'unknown')}': {str(e)}"
            errors.append(error_msg)
            logger.exception("Error creating contact request for agent %s", agent.id)
    
    # Generate the full external URL for the contact requests page
    try:
        current_site = Site.objects.get_current()
        protocol = 'https://'
        relative_url = reverse('agent_contact_requests', kwargs={'pk': agent.id})
        approval_url = f"{protocol}{current_site.domain}{relative_url}"
    except Exception as e:
        logger.warning("Failed to generate contact requests URL for agent %s: %s", agent.id, str(e))
        approval_url = "the agent console"
    
    # Build response message
    parts = []
    
    if created_requests:
        contacts_list = ", ".join([
            f"{c['name']} ({c['address']})" for c in created_requests
        ])
        parts.append(f"Created {len(created_requests)} contact request(s): {contacts_list}")
    
    if already_allowed:
        allowed_list = ", ".join([f"{c['address']}" for c in already_allowed])
        parts.append(f"{len(already_allowed)} contact(s) already allowed: {allowed_list}")
    
    if already_pending:
        pending_list = ", ".join([f"{c['address']}" for c in already_pending])
        parts.append(f"{len(already_pending)} request(s) already pending: {pending_list}")
    
    if errors:
        error_list = "; ".join(errors)
        parts.append(f"Errors: {error_list}")
    
    message = ". ".join(parts)
    
    # Add instruction to message user if any new requests were created
    if created_requests:
        message += f". You must now send a message to the user asking them to approve the contact request(s) at {approval_url}"
    
    # Determine status
    if created_requests and not errors:
        status = "ok"
    elif created_requests and errors:
        status = "partial"
    elif already_allowed and not errors:
        status = "ok"
    else:
        status = "error"
    
    return {
        "status": status,
        "message": message,
        "created_count": len(created_requests),
        "already_allowed_count": len(already_allowed),
        "already_pending_count": len(already_pending),
        "approval_url": approval_url if created_requests else None
    }
