from datetime import datetime
from email.utils import parseaddr
from typing import Callable, List

from django.db.models import Max

from api.services.email_verification import has_verified_email
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES

from ...models import (
    AgentCollaborator,
    CommsAllowlistEntry,
    CommsAllowlistRequest,
    CommsChannel,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentMessage,
    UserPhoneNumber,
)
from .contact_results import ContactSQLiteRecord

DisplayNameFn = Callable[[object], str | None]
CanConfigureFn = Callable[[int | None], bool]
ContactKey = tuple[str, str]
ContactActivityMap = dict[ContactKey, datetime]


def normalize_contact_address(channel: str, address: str) -> str:
    raw = (address or "").strip()
    if channel == CommsChannel.EMAIL:
        return (parseaddr(raw)[1] or raw).strip().lower()
    return raw


def _activity_for(
    activity_by_key: ContactActivityMap,
    channel: str,
    address: str,
) -> datetime | None:
    return activity_by_key.get((channel, normalize_contact_address(channel, address)))


def build_contact_activity_by_key(agent: PersistentAgent) -> ContactActivityMap:
    activity: ContactActivityMap = {}

    def merge(
        channel: str | None,
        address: str | None,
        last_conversed_at: datetime | None,
    ) -> None:
        if not channel or not address or last_conversed_at is None:
            return
        normalized_address = normalize_contact_address(channel, address)
        if not normalized_address:
            return
        key = (channel, normalized_address)
        existing = activity.get(key)
        if existing is None or last_conversed_at > existing:
            activity[key] = last_conversed_at

    inbound_rows = (
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=False,
            from_endpoint__isnull=False,
        )
        .values("from_endpoint__channel", "from_endpoint__address")
        .annotate(last_conversed_at=Max("timestamp"))
    )
    for row in inbound_rows:
        merge(
            row["from_endpoint__channel"],
            row["from_endpoint__address"],
            row["last_conversed_at"],
        )

    outbound_rows = (
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            is_outbound=True,
            to_endpoint__isnull=False,
        )
        .values("to_endpoint__channel", "to_endpoint__address")
        .annotate(last_conversed_at=Max("timestamp"))
    )
    for row in outbound_rows:
        merge(
            row["to_endpoint__channel"],
            row["to_endpoint__address"],
            row["last_conversed_at"],
        )

    conversation_rows = (
        PersistentAgentMessage.objects.filter(
            owner_agent=agent,
            conversation__isnull=False,
        )
        .values("conversation__channel", "conversation__address")
        .annotate(last_conversed_at=Max("timestamp"))
    )
    for row in conversation_rows:
        merge(
            row["conversation__channel"],
            row["conversation__address"],
            row["last_conversed_at"],
        )

    cc_rows = (
        PersistentAgentCommsEndpoint.objects.filter(cc_messages__owner_agent=agent)
        .values("channel", "address")
        .annotate(last_conversed_at=Max("cc_messages__timestamp"))
    )
    for row in cc_rows:
        merge(row["channel"], row["address"], row["last_conversed_at"])

    return activity


def contact_relevance_at(
    *,
    channel: str,
    address: str,
    activity_by_key: ContactActivityMap,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
) -> datetime | None:
    candidates = [
        _activity_for(activity_by_key, channel, address),
        updated_at,
        created_at,
    ]
    return max((candidate for candidate in candidates if candidate is not None), default=None)


def build_contacts_snapshot_records(
    agent: PersistentAgent,
    *,
    display_name_for_user: DisplayNameFn,
    user_can_configure: CanConfigureFn,
    activity_by_key: ContactActivityMap | None = None,
) -> List[ContactSQLiteRecord]:
    allowed_records: dict[tuple[str, str], ContactSQLiteRecord] = {}
    owner_email_verified = has_verified_email(agent.user) if agent.user else False
    if activity_by_key is None:
        activity_by_key = build_contact_activity_by_key(agent)

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
                last_conversed_at=_activity_for(
                    activity_by_key,
                    CommsChannel.EMAIL,
                    agent.user.email,
                ),
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
                    last_conversed_at=_activity_for(
                        activity_by_key,
                        CommsChannel.SMS,
                        phone.phone_number,
                    ),
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
                    last_conversed_at=_activity_for(
                        activity_by_key,
                        CommsChannel.EMAIL,
                        membership.user.email,
                    ),
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
                    last_conversed_at=_activity_for(
                        activity_by_key,
                        CommsChannel.SMS,
                        phone.phone_number,
                    ),
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
                    last_conversed_at=_activity_for(
                        activity_by_key,
                        CommsChannel.EMAIL,
                        collaborator.user.email,
                    ),
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
                    created_at=entry.created_at.isoformat() if entry.created_at else None,
                    updated_at=entry.updated_at.isoformat() if entry.updated_at else None,
                    last_conversed_at=_activity_for(
                        activity_by_key,
                        entry.channel,
                        entry.address,
                    ),
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
            last_conversed_at=_activity_for(
                activity_by_key,
                request.channel,
                request.address,
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
    created_at: str | None = None,
    updated_at: str | None = None,
    last_conversed_at: datetime | None = None,
) -> ContactSQLiteRecord:
    last_conversed_at_iso = last_conversed_at.isoformat() if last_conversed_at else None
    relevance_at = max(
        (
            value
            for value in (
                last_conversed_at_iso,
                updated_at,
                responded_at,
                requested_at,
                created_at,
            )
            if value
        ),
        default=None,
    )
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
        last_conversed_at=last_conversed_at_iso,
        relevance_at=relevance_at,
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
