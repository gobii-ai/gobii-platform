from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from api.models import CommsAllowlistEntry, CommsChannel, PersistentAgent, get_agent_contact_counts
from util.subscription_helper import get_user_max_contacts_per_agent


class AutomaticContactAuthorizationError(Exception):
    pass


@dataclass(frozen=True)
class EmailContactAuthorizationResult:
    addresses: tuple[str, ...]
    created_addresses: tuple[str, ...]
    reactivated_addresses: tuple[str, ...]


def _normalize_email_addresses(addresses) -> list[str]:
    normalized_addresses: list[str] = []
    seen: set[str] = set()
    for raw_address in addresses:
        address = (raw_address or "").strip().lower()
        try:
            validate_email(address)
        except ValidationError as exc:
            raise AutomaticContactAuthorizationError(
                f"Recipient address '{address or raw_address}' is not a valid email address."
            ) from exc
        if address not in seen:
            seen.add(address)
            normalized_addresses.append(address)
    return normalized_addresses


def authorize_email_contacts(agent: PersistentAgent, addresses) -> EmailContactAuthorizationResult:
    """Add otherwise-unauthorized email recipients when the agent owner opted in."""
    normalized_addresses = _normalize_email_addresses(addresses)
    if not normalized_addresses:
        return EmailContactAuthorizationResult((), (), ())

    if agent.contact_approval_mode != PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL:
        raise AutomaticContactAuthorizationError(
            "This agent requires approval before adding new email contacts."
        )

    created_addresses: list[str] = []
    reactivated_addresses: list[str] = []

    with transaction.atomic():
        locked_agent = (
            PersistentAgent.objects.select_for_update()
            .select_related("user", "organization")
            .get(pk=agent.pk)
        )
        if locked_agent.contact_approval_mode != PersistentAgent.ContactApprovalMode.AUTO_APPROVE_EMAIL:
            raise AutomaticContactAuthorizationError(
                "This agent requires approval before adding new email contacts."
            )

        addresses_to_authorize = [
            address
            for address in normalized_addresses
            if not locked_agent.is_recipient_whitelisted(CommsChannel.EMAIL, address)
        ]
        if not addresses_to_authorize:
            return EmailContactAuthorizationResult(tuple(normalized_addresses), (), ())

        existing_entries = {
            entry.address: entry
            for entry in CommsAllowlistEntry.objects.select_for_update().filter(
                agent=locked_agent,
                channel=CommsChannel.EMAIL,
                address__in=addresses_to_authorize,
            )
        }
        slots_needed = sum(
            1
            for address in addresses_to_authorize
            if address not in existing_entries or not existing_entries[address].is_active
        )

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
            entry = existing_entries.get(address)
            if entry is None:
                CommsAllowlistEntry.objects.create(
                    agent=locked_agent,
                    channel=CommsChannel.EMAIL,
                    address=address,
                    is_active=True,
                    allow_inbound=True,
                    allow_outbound=True,
                    can_configure=False,
                )
                created_addresses.append(address)
                continue

            was_inactive = not entry.is_active
            entry.is_active = True
            entry.allow_inbound = True
            entry.allow_outbound = True
            entry.can_configure = False
            entry.save(update_fields=[
                "is_active",
                "allow_inbound",
                "allow_outbound",
                "can_configure",
                "updated_at",
            ])
            if was_inactive:
                reactivated_addresses.append(address)

    return EmailContactAuthorizationResult(
        tuple(normalized_addresses),
        tuple(created_addresses),
        tuple(reactivated_addresses),
    )
