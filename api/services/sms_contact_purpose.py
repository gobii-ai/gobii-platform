import hashlib

from django.conf import settings
from django.db.utils import InternalError, OperationalError, ProgrammingError
from waffle import switch_is_active

from constants.feature_flags import SMS_CONTACT_PURPOSE_REQUIRED
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


SMS_CONTACT_PERMISSION_ATTESTATION_TEXT = (
    "I confirm I have permission to contact this number by SMS."
)


def sms_contact_purpose_required() -> bool:
    """Return whether new SMS contacts must declare compliance metadata."""
    try:
        return switch_is_active(SMS_CONTACT_PURPOSE_REQUIRED)
    except (InternalError, OperationalError, ProgrammingError):
        return False


def sms_contact_address_audit_fingerprint(address: str | None) -> str | None:
    normalized = (address or "").strip()
    if not normalized:
        return None
    payload = f"{settings.SECRET_KEY}:sms-contact:{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def track_sms_contact_approval(
    *,
    user_id,
    agent,
    address: str,
    approval_source: str,
    approval_action: str,
    allow_inbound: bool,
    allow_outbound: bool,
    can_configure: bool = False,
    sms_contact_purpose: str | None = None,
    sms_contact_purpose_details: str | None = None,
    sms_contact_permission_attested: bool | None = None,
    allowlist_entry_id: str | None = None,
    contact_request_id: str | None = None,
) -> None:
    normalized_address = (address or "").strip()
    properties = Analytics.with_org_properties(
        {
            "agent_id": str(agent.pk),
            "agent_name": agent.name,
            "approval_source": approval_source,
            "approval_action": approval_action,
            "channel": "sms",
            "allow_inbound": bool(allow_inbound),
            "allow_outbound": bool(allow_outbound),
            "can_configure": bool(can_configure),
            "sms_contact_purpose": sms_contact_purpose,
            "sms_contact_purpose_details_provided": bool(sms_contact_purpose_details),
            "sms_contact_permission_attested": bool(sms_contact_permission_attested),
            "sms_contact_permission_attestation_text": SMS_CONTACT_PERMISSION_ATTESTATION_TEXT,
            "contact_address_last4": normalized_address[-4:] if normalized_address else None,
            "contact_address_fingerprint": sms_contact_address_audit_fingerprint(normalized_address),
        },
        organization=getattr(agent, "organization", None),
    )
    if allowlist_entry_id:
        properties["allowlist_entry_id"] = str(allowlist_entry_id)
    if contact_request_id:
        properties["contact_request_id"] = str(contact_request_id)

    Analytics.track_event(
        user_id=user_id,
        event=AnalyticsEvent.AGENT_SMS_CONTACT_APPROVED,
        source=AnalyticsSource.WEB,
        properties=properties,
    )
