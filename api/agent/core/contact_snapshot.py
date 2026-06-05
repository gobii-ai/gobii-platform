from email.utils import parseaddr
from typing import Callable, List

from api.services.email_verification import has_verified_email
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES

from ...models import (
    AgentCollaborator,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    OrganizationMembership,
    PersistentAgent,
    UserPhoneNumber,
)
from .contact_results import ContactSQLiteRecord

DisplayNameFn = Callable[[object], str | None]
CanConfigureFn = Callable[[int | None], bool]


def normalize_contact_address(channel: str, address: str) -> str:
    raw = (address or "").strip()
    if channel == CommsChannel.EMAIL:
        return (parseaddr(raw)[1] or raw).strip().lower()
    return raw


def build_contacts_snapshot_records(
    agent: PersistentAgent,
    *,
    display_name_for_user: DisplayNameFn,
    user_can_configure: CanConfigureFn,
) -> List[ContactSQLiteRecord]:
    allowed_records: dict[tuple[str, str], ContactSQLiteRecord] = {}
    owner_email_verified = has_verified_email(agent.user) if agent.user else False

    def add_allowed(record: ContactSQLiteRecord) -> None:
        if record.normalized_address:
            allowed_records[(record.channel, record.normalized_address)] = record

    if owner_email_verified and agent.user and agent.user.email:
        add_allowed(
            _contact_record(
                contact_id=f"owner:email:{agent.user_id}",
                channel=CommsChannel.EMAIL,
                address=agent.user.email,
                display_name=display_name_for_user(agent.user) or "",
                source="owner",
                status="allowed",
                allow_inbound=True,
                allow_outbound=True,
                can_configure=user_can_configure(agent.user_id),
            )
        )
        owner_phones = UserPhoneNumber.objects.filter(
            user=agent.user,
            is_verified=True,
        ).order_by("phone_number")
        for phone in owner_phones:
            add_allowed(
                _contact_record(
                    contact_id=f"owner:sms:{phone.id}",
                    channel=CommsChannel.SMS,
                    address=phone.phone_number,
                    display_name=display_name_for_user(agent.user) or "",
                    source="owner",
                    status="allowed",
                    allow_inbound=True,
                    allow_outbound=True,
                    can_configure=user_can_configure(agent.user_id),
                )
            )

    if owner_email_verified and agent.organization_id:
        org_user_ids: list[int] = []
        memberships = (
            OrganizationMembership.objects.filter(
                org_id=agent.organization_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                user__email__isnull=False,
            )
            .exclude(user__email="")
            .select_related("user")
            .order_by("user__email")
        )
        for membership in memberships:
            org_user_ids.append(membership.user_id)
            add_allowed(
                _contact_record(
                    contact_id=f"org_member:email:{membership.id}",
                    channel=CommsChannel.EMAIL,
                    address=membership.user.email,
                    display_name=display_name_for_user(membership.user) or "",
                    source="org_member",
                    status="allowed",
                    allow_inbound=True,
                    allow_outbound=True,
                    can_configure=membership.role in ORG_AGENT_CONFIG_AUTHORITY_ROLES,
                )
            )

        org_phones = (
            UserPhoneNumber.objects.filter(
                user_id__in=org_user_ids,
                is_verified=True,
            )
            .select_related("user")
            .order_by("phone_number")
        )
        for phone in org_phones:
            add_allowed(
                _contact_record(
                    contact_id=f"org_member:sms:{phone.id}",
                    channel=CommsChannel.SMS,
                    address=phone.phone_number,
                    display_name=display_name_for_user(phone.user) or "",
                    source="org_member",
                    status="allowed",
                    allow_inbound=True,
                    allow_outbound=False,
                    can_configure=user_can_configure(phone.user_id),
                )
            )

    if owner_email_verified:
        collaborators = (
            AgentCollaborator.objects.filter(agent=agent, user__email__isnull=False)
            .exclude(user__email="")
            .select_related("user")
            .order_by("user__email")
        )
        for collaborator in collaborators:
            add_allowed(
                _contact_record(
                    contact_id=f"collaborator:email:{collaborator.id}",
                    channel=CommsChannel.EMAIL,
                    address=collaborator.user.email,
                    display_name=display_name_for_user(collaborator.user) or "",
                    source="collaborator",
                    status="allowed",
                    allow_inbound=True,
                    allow_outbound=True,
                    can_configure=user_can_configure(collaborator.user_id),
                    updated_at=collaborator.created_at.isoformat() if collaborator.created_at else None,
                )
            )

        allowlist_entries = (
            CommsAllowlistEntry.objects.filter(agent=agent, is_active=True)
            .order_by("channel", "address")
        )
        for entry in allowlist_entries:
            add_allowed(
                _contact_record(
                    contact_id=f"allowlist_entry:{entry.id}",
                    channel=entry.channel,
                    address=entry.address,
                    source="allowlist_entry",
                    status="allowed",
                    allow_inbound=entry.allow_inbound,
                    allow_outbound=entry.allow_outbound,
                    can_configure=entry.can_configure,
                    updated_at=entry.updated_at.isoformat() if entry.updated_at else None,
                )
            )

    records = dict(allowed_records)
    contact_requests = (
        CommsAllowlistRequest.objects.filter(agent=agent)
        .order_by("-requested_at")
    )
    for request in contact_requests:
        request_record = _contact_record(
            contact_id=f"contact_request:{request.id}",
            channel=request.channel,
            address=request.address,
            display_name=request.name or "",
            source="contact_request",
            status=_contact_request_status(request),
            allow_inbound=False,
            allow_outbound=False,
            can_configure=False,
            requested_at=request.requested_at.isoformat() if request.requested_at else None,
            responded_at=request.responded_at.isoformat() if request.responded_at else None,
            updated_at=request.responded_at.isoformat() if request.responded_at else (
                request.requested_at.isoformat() if request.requested_at else None
            ),
        )
        key = (request_record.channel, request_record.normalized_address)
        if request_record.normalized_address and key not in allowed_records and key not in records:
            records[key] = request_record

    return sorted(
        records.values(),
        key=lambda record: (record.channel, record.normalized_address, record.source),
    )


def _contact_record(
    *,
    contact_id: str,
    channel: str,
    address: str,
    display_name: str = "",
    source: str,
    status: str,
    allow_inbound: bool,
    allow_outbound: bool,
    can_configure: bool = False,
    requested_at: str | None = None,
    responded_at: str | None = None,
    updated_at: str | None = None,
) -> ContactSQLiteRecord:
    return ContactSQLiteRecord(
        contact_id=contact_id,
        channel=channel,
        address=(address or "").strip(),
        normalized_address=normalize_contact_address(channel, address),
        display_name=display_name or "",
        source=source,
        status=status,
        allow_inbound=allow_inbound,
        allow_outbound=allow_outbound,
        can_configure=can_configure,
        requested_at=requested_at,
        responded_at=responded_at,
        updated_at=updated_at,
    )


def _contact_request_status(request: CommsAllowlistRequest) -> str:
    if request.status == CommsAllowlistRequest.RequestStatus.PENDING:
        return "expired_request" if request.is_expired() else "pending_request"
    if request.status == CommsAllowlistRequest.RequestStatus.REJECTED:
        return "rejected_request"
    if request.status == CommsAllowlistRequest.RequestStatus.EXPIRED:
        return "expired_request"
    if request.status == CommsAllowlistRequest.RequestStatus.APPROVED:
        return "approved_missing_allowlist"
    return f"{request.status}_request"
