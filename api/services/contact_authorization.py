from django.db import transaction

from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    get_agent_contact_counts,
)
from util.subscription_helper import get_user_max_contacts_per_agent


class AutomaticContactAuthorizationError(Exception):
    pass


def _normalize_email_addresses(addresses) -> list[str]:
    return list(dict.fromkeys(
        normalized
        for address in addresses
        if (
            normalized := PersistentAgentCommsEndpoint.normalize_address(
                CommsChannel.EMAIL,
                address,
            )
        )
    ))


def authorize_email_contacts(agent: PersistentAgent, addresses) -> None:
    """Add otherwise-unauthorized email recipients when the agent owner opted in."""
    normalized_addresses = _normalize_email_addresses(addresses)
    if not normalized_addresses:
        return

    with transaction.atomic():
        # PostgreSQL cannot lock the nullable side of the organization outer join.
        locked_agent = PersistentAgent.objects.select_for_update().get(pk=agent.pk)
        if locked_agent.contact_approval_mode != PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL:
            raise AutomaticContactAuthorizationError(
                "This agent requires approval before adding new email contacts."
            )

        addresses_to_authorize = [
            address
            for address in normalized_addresses
            if not locked_agent.is_recipient_whitelisted(CommsChannel.EMAIL, address)
        ]
        agent.whitelist_policy = locked_agent.whitelist_policy
        if not addresses_to_authorize:
            return

        active_addresses = set(
            CommsAllowlistEntry.objects.select_for_update().filter(
                agent=locked_agent,
                channel=CommsChannel.EMAIL,
                address__in=addresses_to_authorize,
                is_active=True,
            ).values_list("address", flat=True)
        )
        slots_needed = len(addresses_to_authorize) - len(active_addresses)

        contact_cap = get_user_max_contacts_per_agent(
            locked_agent.user,
            organization=locked_agent.organization,
        )
        contact_counts = get_agent_contact_counts(locked_agent)
        if contact_cap > 0 and contact_counts is not None:
            available_slots = max(contact_cap - contact_counts["total"], 0)
            if slots_needed > available_slots:
                raise AutomaticContactAuthorizationError(
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
                    "allow_inbound": True,
                    "allow_outbound": True,
                    "can_configure": False,
                },
            )

        if locked_agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
            locked_agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
            locked_agent.save(update_fields=["whitelist_policy"])

    # send_email reuses this instance for its post-authorization allowlist check.
    agent.whitelist_policy = locked_agent.whitelist_policy
