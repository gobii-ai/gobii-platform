"""
Request contact permission tool for persistent agents.

This tool allows agents to request permission to contact people
who are not yet in their allowlist. The agent owner must approve
these requests before the agent can send messages.
"""
import logging
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.core import signing
from django.template.loader import render_to_string
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
                "Request permission to contact someone via email or SMS who is not in your allowlist. "
                "Creates a request that the user must approve before you can contact them. "
                "A notification email with approval buttons is sent automatically to the agent owner. "
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


def _build_base_url():
    """Return the base URL (https://domain) using the current Site."""
    try:
        current_site = Site.objects.get_current()
        return f"https://{current_site.domain}"
    except Exception:
        return ""


def _send_approval_email(agent, new_requests):
    """Send a styled HTML notification email to the agent owner with approve/deny buttons."""
    owner_email = (agent.user.email or "").strip()
    if not owner_email:
        logger.warning("Agent %s owner has no email; skipping contact-request notification", agent.id)
        return

    base_url = _build_base_url()

    # Build per-request context dicts with approve/deny URLs
    request_contexts = []
    for req in new_requests:
        approve_url = f"{base_url}{reverse('contact_request_approve', kwargs={'token': req.approval_token})}"
        deny_url = f"{base_url}{reverse('contact_request_deny', kwargs={'token': req.denial_token})}"
        request_contexts.append({
            "name": req.name,
            "address": req.address,
            "purpose": req.purpose,
            "reason": req.reason,
            "approve_url": approve_url,
            "deny_url": deny_url,
        })

    # Build bulk tokens for Approve All / Deny All
    request_ids = [req.pk for req in new_requests]
    approve_all_token = signing.dumps(
        {"agent_id": str(agent.pk), "request_ids": [str(r) for r in request_ids], "action": "approve_all"},
        salt="contact_request_bulk",
    )
    deny_all_token = signing.dumps(
        {"agent_id": str(agent.pk), "request_ids": [str(r) for r in request_ids], "action": "deny_all"},
        salt="contact_request_bulk",
    )
    approve_all_url = f"{base_url}{reverse('contact_request_approve_all', kwargs={'token': approve_all_token})}"
    deny_all_url = f"{base_url}{reverse('contact_request_deny_all', kwargs={'token': deny_all_token})}"
    review_url = f"{base_url}{reverse('agent_contact_requests', kwargs={'pk': agent.id})}"

    context = {
        "agent": agent,
        "contact_requests": request_contexts,
        "approve_all_url": approve_all_url,
        "deny_all_url": deny_all_url,
        "review_url": review_url,
    }

    html_body = render_to_string("emails/contact_approval_request.html", context)
    text_body = (
        f"Your agent '{agent.name}' is requesting permission to contact "
        f"{len(new_requests)} person(s). Review requests at: {review_url}"
    )

    n = len(new_requests)
    subject = (
        f"{agent.name} wants to contact {new_requests[0].name or new_requests[0].address}"
        if n == 1
        else f"{agent.name} wants to contact {n} people"
    )

    try:
        send_mail(
            subject=subject,
            message=text_body,
            from_email=None,
            recipient_list=[owner_email],
            html_message=html_body,
            fail_silently=False,
        )
        logger.info("Sent contact-request approval email for agent %s to %s", agent.id, owner_email)
    except Exception:
        logger.exception("Failed to send contact-request approval email for agent %s", agent.id)


def execute_request_contact_permission(agent: PersistentAgent, params: dict) -> dict:
    """Create contact permission requests for the agent.
    
    This tool allows agents to request permission to contact people
    who are not yet in their allowlist. The requests are created as
    CommsAllowlistRequest records that the user must approve.
    A styled notification email is sent to the agent owner automatically.
    """
    contacts = params.get("contacts")
    if not contacts or not isinstance(contacts, list):
        return {"status": "error", "message": "Missing or invalid required parameter: contacts"}
    
    if not contacts:
        return {"status": "error", "message": "At least one contact must be specified"}
    
    # Track full model objects so we can use tokens for the email
    created_request_objects = []
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
            
            # Create the contact request (expires in 7 days)
            expires_at = timezone.now() + timedelta(days=7)
            
            request_obj = CommsAllowlistRequest.objects.create(
                agent=agent,
                channel=channel_enum,
                address=address,
                name=name,
                reason=reason,
                purpose=purpose,
                expires_at=expires_at
            )
            
            created_request_objects.append(request_obj)
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
    
    # Send a notification email if new requests were created
    email_sent = False
    if created_request_objects:
        try:
            _send_approval_email(agent, created_request_objects)
            email_sent = True
        except Exception:
            logger.exception("Unexpected error sending approval email for agent %s", agent.id)

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

    # Build the review URL for the agent console (also shown in the frontend tool details panel)
    try:
        base_url = _build_base_url()
        review_url = f"{base_url}{reverse('agent_contact_requests', kwargs={'pk': agent.id})}"
    except Exception:
        review_url = None

    if created_requests:
        if email_sent:
            message += ". A notification email with approval buttons has been sent to the agent owner."
        else:
            # Fall back if email sending failed
            approval_ref = review_url or "the agent console"
            message += f". Please ask the user to approve the contact request(s) at {approval_ref}"

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
        "email_sent": email_sent,
        # Keep approval_url for the agent console tool-details panel
        "approval_url": review_url if created_requests else None,
    }
