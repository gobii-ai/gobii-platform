from dataclasses import dataclass
from email.utils import parseaddr

from allauth.account.models import EmailAddress
from django.db import transaction
from waffle import flag_is_active

from api.models import (
    CommsAllowlistEntry,
    CommsChannel,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    UserPreference,
)
from constants.feature_flags import EMAIL_REVIEW_OUTBOX


ORGANIZATION_DEFAULT_KEY = "default_email_sending_mode"
ORGANIZATION_MINIMUM_KEY = "minimum_email_sending_mode"
MODE_STRICTNESS = {
    PersistentAgent.EmailSendingMode.SEND_AUTOMATICALLY: 0,
    PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS: 1,
    PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL: 2,
}


@dataclass(frozen=True, slots=True)
class RecipientPolicyDecision:
    normalized_recipients: tuple[str, ...]
    internal_recipients: tuple[str, ...]
    external_recipients: tuple[str, ...]
    unknown_external_recipients: tuple[str, ...]
    blocked_recipients: tuple[str, ...]
    requires_review: bool
    effective_mode: str


def email_review_outbox_enabled() -> bool:
    return flag_is_active(None, EMAIL_REVIEW_OUTBOX)


def normalize_email_address(address: str | None) -> str:
    parsed = parseaddr(address or "")[1] or (address or "")
    return PersistentAgentCommsEndpoint.normalize_address(CommsChannel.EMAIL, parsed) or ""


def normalize_email_addresses(addresses) -> tuple[str, ...]:
    return tuple(dict.fromkeys(filter(None, (normalize_email_address(address) for address in addresses))))


def _valid_mode(value: object) -> str | None:
    if isinstance(value, str) and value in MODE_STRICTNESS:
        return value
    return None


def get_workspace_default_email_sending_mode(*, user, organization=None) -> str:
    if organization is not None:
        settings_value = organization.org_settings if isinstance(organization.org_settings, dict) else {}
        return _valid_mode(settings_value.get(ORGANIZATION_DEFAULT_KEY)) or PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL

    preferences = UserPreference.resolve_known_preferences(user)
    return preferences[UserPreference.KEY_DEFAULT_EMAIL_SENDING_MODE]


def get_organization_minimum_email_sending_mode(organization) -> str | None:
    if organization is None:
        return None
    org_settings = organization.org_settings if isinstance(organization.org_settings, dict) else {}
    return _valid_mode(org_settings.get(ORGANIZATION_MINIMUM_KEY))


def get_effective_email_sending_mode(agent: PersistentAgent) -> str:
    requested = _valid_mode(agent.email_sending_mode) or PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL
    minimum = get_organization_minimum_email_sending_mode(agent.organization)
    if minimum and MODE_STRICTNESS[minimum] > MODE_STRICTNESS[requested]:
        return minimum
    return requested


def get_verified_internal_email_addresses(agent: PersistentAgent) -> frozenset[str]:
    if agent.organization_id:
        user_ids = OrganizationMembership.objects.filter(
            org_id=agent.organization_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).values_list("user_id", flat=True)
    else:
        user_ids = [agent.user_id]

    return frozenset(
        normalize_email_address(address)
        for address in EmailAddress.objects.filter(
            user_id__in=user_ids,
            verified=True,
        ).values_list("email", flat=True)
        if address
    )


def classify_email_recipients(agent: PersistentAgent, recipients) -> RecipientPolicyDecision:
    normalized = normalize_email_addresses(recipients)
    internal_addresses = get_verified_internal_email_addresses(agent)
    internal = tuple(address for address in normalized if address in internal_addresses)
    external = tuple(address for address in normalized if address not in internal_addresses)

    all_entries = {
        entry.address.lower(): entry
        for entry in CommsAllowlistEntry.objects.filter(
            agent=agent,
            channel=CommsChannel.EMAIL,
            address__in=external,
        ).only("address", "allow_outbound", "is_active")
    }
    blocked = tuple(
        address
        for address in external
        if address in all_entries
        and (not all_entries[address].is_active or not all_entries[address].allow_outbound)
    )
    unknown = tuple(address for address in external if address not in all_entries)
    known_allowed = tuple(
        address
        for address in external
        if address in all_entries
        and all_entries[address].is_active
        and all_entries[address].allow_outbound
    )
    effective_mode = get_effective_email_sending_mode(agent)
    requires_review = bool(
        external
        and (
            effective_mode == PersistentAgent.EmailSendingMode.REVIEW_ALL_EXTERNAL
            or (
                effective_mode == PersistentAgent.EmailSendingMode.REVIEW_NEW_CONTACTS
                and not known_allowed == external
            )
        )
    )

    return RecipientPolicyDecision(
        normalized_recipients=normalized,
        internal_recipients=internal,
        external_recipients=external,
        unknown_external_recipients=unknown,
        blocked_recipients=blocked,
        requires_review=requires_review,
        effective_mode=effective_mode,
    )


@transaction.atomic
def set_workspace_email_sending_policy(
    *,
    user,
    organization,
    default_mode: str,
    minimum_mode: str | None = None,
    apply_to_existing: bool = False,
) -> None:
    default_mode = _valid_mode(default_mode)
    minimum_mode = _valid_mode(minimum_mode) if minimum_mode else None
    if not default_mode:
        raise ValueError("Invalid default email sending mode.")

    if organization is None:
        UserPreference.update_known_preferences(
            user,
            {UserPreference.KEY_DEFAULT_EMAIL_SENDING_MODE: default_mode},
        )
        agents = PersistentAgent.objects.filter(user=user, organization__isnull=True, is_deleted=False)
    else:
        org_settings = dict(organization.org_settings or {})
        org_settings[ORGANIZATION_DEFAULT_KEY] = default_mode
        if minimum_mode:
            org_settings[ORGANIZATION_MINIMUM_KEY] = minimum_mode
        else:
            org_settings.pop(ORGANIZATION_MINIMUM_KEY, None)
        organization.org_settings = org_settings
        organization.save(update_fields=["org_settings", "updated_at"])
        agents = PersistentAgent.objects.filter(organization=organization, is_deleted=False)

    if apply_to_existing:
        agents.update(email_sending_mode=default_mode)
