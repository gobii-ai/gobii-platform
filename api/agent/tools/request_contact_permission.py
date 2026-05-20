"""
Request contact permission tool for persistent agents.

This tool allows agents to request permission to contact people
who are not yet in their allowlist. The agent owner must approve
these requests before the agent can send messages.
"""
import logging
from django.utils import timezone
from datetime import timedelta

from ...models import (
    PersistentAgent, 
    CommsAllowlistEntry, 
    CommsAllowlistRequest,
    CommsChannel,
    SmsContactPurpose,
)
from api.services.sms_contact_purpose import sms_contact_purpose_required
from util.urls import (
    build_immersive_contact_requests_path,
    build_immersive_contact_requests_site_url,
)

logger = logging.getLogger(__name__)


def get_request_contact_permission_tool() -> dict:
    """Return the tool definition for requesting contact permission."""
    return {
        "type": "function",
        "function": {
            "name": "request_contact_permission",
            "description": (
                "Request user approval before emailing or texting a specific contact not in your allowlist. "
                "Use this instead of request_human_input for email/SMS contact approval. "
                "Creates a request that the user must approve before you can contact them. "
                "Returns a URL that you MUST send to the user so they can approve the contact. "
                "Check if contact already exists before requesting. If the user just gave you a specific email "
                "address or phone number and it is not already shown in your allowed contacts, request permission "
                "before reading files, searching, drafting, or asking non-blocking follow-up questions. "
                "For setup-only recurring work where the user explicitly says not to send the first email/SMS now, "
                "do not request contact permission during setup; record the recipient in the charter and request "
                "permission only when an actual outbound send is needed. "
                "Only use an email or phone number the user has previously provided to you, or that is publicly available. "
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
                                },
                                "sms_contact_purpose": {
                                    "type": "string",
                                    "enum": list(SmsContactPurpose.values),
                                    "description": "Required for SMS contacts when enabled: operational purpose for using SMS instead of email."
                                },
                                "sms_contact_purpose_details": {
                                    "type": "string",
                                    "description": "Optional additional operational context for SMS contact approval."
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
    
    This tool allows agents to request permission to contact people
    who are not yet in their allowlist. The requests are created as
    CommsAllowlistRequest records that the user must approve.
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
            sms_purpose = (contact.get("sms_contact_purpose") or "").strip() or None
            sms_purpose_details = (contact.get("sms_contact_purpose_details") or "").strip() or None
            
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
                sms_purpose = None
                sms_purpose_details = None
            else:
                address = address.strip()
                if sms_purpose and sms_purpose not in SmsContactPurpose.values:
                    errors.append(
                        f"Invalid SMS contact purpose '{sms_purpose}' for {address}."
                    )
                    continue
                if sms_contact_purpose_required() and not sms_purpose:
                    errors.append(
                        f"SMS contact {address} requires an operational purpose before it can be requested."
                    )
                    continue
            
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
            
            CommsAllowlistRequest.objects.create(
                agent=agent,
                channel=channel_enum,
                address=address,
                name=name,
                reason=reason,
                purpose=purpose,
                expires_at=expires_at,
                sms_contact_purpose=sms_purpose,
                sms_contact_purpose_details=sms_purpose_details,
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
        approval_url = build_immersive_contact_requests_site_url(
            agent.id,
            str(agent.organization_id) if agent.organization_id else None,
        )
    except Exception:
        logger.warning(
            "Failed to generate contact requests URL for agent %s; returning relative fallback",
            agent.id,
            exc_info=True,
        )
        approval_url = build_immersive_contact_requests_path(agent.id)
    
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
