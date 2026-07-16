"""
Request contact permission tool for persistent agents.

This tool allows agents to request permission to contact people
who are not yet in their allowlist. Email contacts may be approved
automatically when the owner has enabled that mode; SMS never is.
"""
import logging
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from datetime import timedelta

from ...models import PersistentAgent, CommsAllowlistEntry, CommsAllowlistRequest, CommsChannel, SmsContactPurpose
from api.services.contact_authorization import (
    AutomaticContactAuthorizationError,
    authorize_email_contacts,
)
from api.services.sms_contact_purpose import sms_contact_purpose_required
from util.urls import build_immersive_contact_requests_path, build_immersive_contact_requests_site_url

logger = logging.getLogger(__name__)


def get_request_contact_permission_tool(agent: PersistentAgent | None = None) -> dict:
    """Return the tool definition for requesting contact permission."""
    auto_approve_email = bool(
        agent
        and agent.contact_approval_mode == PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
    )
    if auto_approve_email:
        description = (
            "Request approval before texting a specific contact not in your allowlist. "
            "New email recipients do not need this tool: call send_email directly and they will be added automatically. "
            "If this tool is called for a new email contact, the contact is approved immediately without a review link. "
            "SMS contacts still require human approval, and pending SMS requests return a URL you MUST send to the user. "
            "Use only user-provided or public contact details; do not guess."
        )
    else:
        description = (
            "Request approval before emailing/texting a specific contact not in your allowlist. "
            "Use this instead of request_human_input for email/SMS contact approval. "
            "Returns a URL you MUST send so the user can approve. "
            "Check allowed contacts first; if the user just gave a specific email/phone not already allowed, request before reading files, searching, drafting, or non-blocking follow-up. "
            "For setup-only recurring work where the user explicitly says not to send the first email/SMS now, "
            "do not request contact permission during setup; record the recipient and request only when an actual outbound send is needed. "
            "Use only user-provided or public contact details; do not guess."
        )
    return {
        "type": "function",
        "function": {
            "name": "request_contact_permission",
            "description": description,
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
                                    "description": "email or sms."
                                },
                                "address": {
                                    "type": "string", 
                                    "description": "Email or E.164 phone."
                                },
                                "name": {
                                    "type": "string", 
                                    "description": "Optional contact name."
                                },
                                "reason": {
                                    "type": "string", 
                                    "description": "Why contact is needed."
                                },
                                "purpose": {
                                    "type": "string", 
                                    "description": "Brief purpose."
                                },
                                "sms_contact_purpose": {
                                    "type": "string",
                                    "enum": list(SmsContactPurpose.values),
                                    "description": "Required for SMS when enabled: operational purpose."
                                },
                                "sms_contact_purpose_details": {
                                    "type": "string",
                                    "description": "Optional SMS approval context."
                                }
                            },
                            "required": ["channel", "address", "reason", "purpose"]
                        },
                        "description": "Contacts to request permission for."
                    }
                },
                "required": ["contacts"]
            }
        }
    }


def execute_request_contact_permission(agent: PersistentAgent, params: dict) -> dict:
    """Create contact permission requests for the agent.
    
    This tool creates CommsAllowlistRequest records for new contacts.
    Eligible email requests are approved immediately when configured;
    all other requests remain pending for the user.
    """
    contacts = params.get("contacts")
    if not contacts or not isinstance(contacts, list):
        return {"status": "error", "message": "Missing or invalid required parameter: contacts"}
    
    if not contacts:
        return {"status": "error", "message": "At least one contact must be specified"}
    
    created_requests = []
    auto_approved = []
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
                allowed_values = ", ".join(SmsContactPurpose.values)
                if sms_purpose and sms_purpose not in SmsContactPurpose.values:
                    errors.append(
                        f"Invalid SMS contact purpose '{sms_purpose}' for {address}. "
                        f"Use one of: {allowed_values}."
                    )
                    continue
                if sms_contact_purpose_required() and not sms_purpose:
                    errors.append(
                        f"SMS contact {address} requires sms_contact_purpose before it can be requested. "
                        f"Use one of: {allowed_values}."
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

            if (
                channel_enum == CommsChannel.EMAIL
                and agent.contact_approval_mode == PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
            ):
                try:
                    with transaction.atomic():
                        request_obj = CommsAllowlistRequest.objects.create(
                            agent=agent,
                            channel=channel_enum,
                            address=address,
                            name=name,
                            reason=reason,
                            purpose=purpose,
                            expires_at=timezone.now() + timedelta(days=7),
                        )
                        authorize_email_contacts(agent, [address])
                        request_obj.approve(invited_by=agent.user, skip_invitation=True)
                    auto_approved.append({
                        "address": address,
                        "channel": channel,
                        "name": name or "Unknown",
                        "purpose": purpose,
                    })
                except (AutomaticContactAuthorizationError, IntegrityError, ValidationError, ValueError) as exc:
                    errors.append(f"Failed to automatically allow '{address}': {exc}")
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

    if auto_approved:
        contacts_list = ", ".join([
            f"{c['name']} ({c['address']})" for c in auto_approved
        ])
        parts.append(f"Automatically allowed {len(auto_approved)} email contact(s): {contacts_list}")
    
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
    if (created_requests or auto_approved) and not errors:
        status = "ok"
    elif (created_requests or auto_approved) and errors:
        status = "partial"
    elif already_allowed and not errors:
        status = "ok"
    else:
        status = "error"
    
    return {
        "status": status,
        "message": message,
        "created_count": len(created_requests),
        "auto_approved_count": len(auto_approved),
        "already_allowed_count": len(already_allowed),
        "already_pending_count": len(already_pending),
        "approval_url": approval_url if created_requests else None
    }
