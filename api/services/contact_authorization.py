from django.db import transaction

from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    get_agent_contact_counts,
)
from api.services.outbound_email_policy import (
    email_review_outbox_enabled,
    get_effective_email_sending_mode,
    normalize_email_addresses,
)
from util.subscription_helper import get_user_max_contacts_per_agent


class ContactAuthorizationError(Exception):
    pass


class AutomaticContactAuthorizationError(ContactAuthorizationError):
    pass


def _authorize_email_contacts(
    agent: PersistentAgent,
    addresses,
    *,
    automatic: bool,
) -> None:
    normalized_addresses = normalize_email_addresses(addresses)
    if not normalized_addresses:
        return
    error_class = (
        AutomaticContactAuthorizationError
        if automatic
        else ContactAuthorizationError
    )

    with transaction.atomic():
        # PostgreSQL cannot lock the nullable side of the organization outer join.
        locked_agent = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
        if automatic:
            automatically_authorized = (
                get_effective_email_sending_mode(locked_agent)
                == PersistentAgent.EmailSendingMode.SEND_AUTOMATICALLY
                if email_review_outbox_enabled()
                else locked_agent.contact_approval_mode
                == PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL
            )
            if not automatically_authorized:
                raise error_class(
                    "This agent requires approval before adding new email contacts."
                )

        existing_contacts = {
            contact.address: contact
            for contact in CommsAllowlistEntry.objects.select_for_update().filter(
                agent=locked_agent,
                channel=CommsChannel.EMAIL,
                address__in=normalized_addresses,
            )
        }
        addresses_to_authorize = []
        for address in normalized_addresses:
            contact = existing_contacts.get(address)
            internal = locked_agent.is_internal_responder_identity(CommsChannel.EMAIL, address)
            blocked = contact and (
                (contact.is_active and not contact.allow_outbound)
                or (not contact.is_active and not automatic)
            )
            if blocked and not internal:
                raise error_class(
                    f"Outbound email is disabled for contact '{address}'. "
                    "The owner can enable it in Contacts & Access."
                )
            if contact and (contact.is_active or not automatic):
                continue
            if contact is None and locked_agent.is_recipient_whitelisted(CommsChannel.EMAIL, address):
                continue
            addresses_to_authorize.append(address)
        agent.whitelist_policy = locked_agent.whitelist_policy
        if not addresses_to_authorize:
            return

        slots_needed = len(addresses_to_authorize)
        contact_cap = get_user_max_contacts_per_agent(
            locked_agent.user,
            organization=locked_agent.organization,
        )
        contact_counts = get_agent_contact_counts(locked_agent)
        if contact_cap > 0 and contact_counts is None:
            raise error_class("Unable to verify this agent's available contact slots.")
        if contact_cap > 0:
            available_slots = max(contact_cap - contact_counts["total"], 0)
            if slots_needed > available_slots:
                raise error_class(
                    f"Cannot add {slots_needed} new email contact(s). "
                    f"This agent has {available_slots} of {contact_cap} contact slots available."
                )

        for address in addresses_to_authorize:
            CommsAllowlistEntry.objects.update_or_create(
                agent=locked_agent,
                channel=CommsChannel.EMAIL,
                address=address,
                defaults={
                    "is_active": True,
                    "allow_inbound": automatic,
                    "allow_outbound": True,
                    "can_configure": False,
                },
            )

        if locked_agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
            locked_agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
            locked_agent.save(update_fields=["whitelist_policy"])

    # send_email reuses this instance for its post-authorization allowlist check.
    agent.whitelist_policy = locked_agent.whitelist_policy


def authorize_email_contacts(agent: PersistentAgent, addresses) -> None:
    """Add otherwise-unauthorized email recipients when the agent owner opted in."""
    _authorize_email_contacts(
        agent,
        addresses,
        automatic=True,
    )


def authorize_reviewed_email_contacts(agent: PersistentAgent, addresses) -> None:
    """Add human-approved email recipients as outbound-only contacts."""
    _authorize_email_contacts(
        agent,
        addresses,
        automatic=False,
    )
