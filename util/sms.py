import logging
import random
from typing import Optional
from opentelemetry import trace
from django.conf import settings
from api.models import SmsNumber, PersistentAgentCommsEndpoint, CommsChannel, UserPhoneNumber
from util.sms_encoding import optimize_sms_for_cost
from util.integrations import twilio_status, twilio_verify_available
from observability import traced
from twilio.base.exceptions import TwilioRestException

try:
    from twilio.rest import Client
except ImportError:  # pragma: no cover - dependency optional in tests
    Client = None  # type: ignore

logger = logging.getLogger(__name__)

tracer = trace.get_tracer("gobii.utils")

TWILIO_RISK_CHECK_DISABLE = "disable"

# ── Vanity helpers ────────────────────────────────────────────────────────────
_T9 = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    "22233344455566677778889999")  # A→2, B→2, …, Z→9

def vanity_to_digits(text: str) -> str:
    """Convert vanity word to its numeric keypad representation (GOBII → 46244)."""
    return text.upper().translate(_T9)

def ends_with_vanity(phone_number: str, vanity: str) -> bool:
    """True if E.164 number ends with the vanity’s digits."""
    return phone_number.endswith(vanity_to_digits(vanity))


def _get_client() -> Optional[Client]:
    status = twilio_status()
    if not status.enabled:
        logger.debug("Twilio client requested while disabled: %s", status.reason)
        return None
    if Client is None:
        logger.warning("Twilio SDK not installed; SMS operations disabled.")
        return None
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    if not account_sid or not auth_token:
        logger.warning("Twilio credentials missing despite enabled flag; skipping client creation.")
        return None
    return Client(account_sid, auth_token)

@tracer.start_as_current_span("SMS start_verification")
def start_verification(phone_number: str) -> Optional[str]:
    """Start an SMS verification; returns verification SID if sent."""
    if not twilio_verify_available():
        logger.warning("Twilio verification service not available; skipping verification send.")
        return None
    service_sid = settings.TWILIO_VERIFY_SERVICE_SID
    client = _get_client()
    if not client or not service_sid:
        logger.warning("Twilio client not configured; skipping verification send.")
        return None
    ver = client.verify.v2.services(service_sid).verifications.create(to=phone_number, channel="sms")
    return ver.sid

@tracer.start_as_current_span("SMS check_verification")
def check_verification(phone_number: str, code: str) -> bool:
    """Check a verification code; returns True if approved."""
    if not twilio_verify_available():
        logger.warning("Twilio verification service not available; skipping verification check.")
        return False
    service_sid = settings.TWILIO_VERIFY_SERVICE_SID
    client = _get_client()
    if not client or not service_sid:
        logger.warning("Twilio client not configured; skipping verification check.")
        return False
    result = client.verify.v2.services(service_sid).verification_checks.create(to=phone_number, code=code)
    return result.status == "approved"

@tracer.start_as_current_span("SMS find_unused_number")
def find_unused_number() -> SmsNumber:
    """
    Find an unused SMS number for the user.
    Returns a SmsNumber instance or raises an exception if none available.
    """
    # Only allocate numbers that are still active in local inventory and were never retired.
    sms_in_use = PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.SMS,
    ).values_list('address', flat=True)

    unused_pks = list(
        SmsNumber.objects.filter(
            is_active=True,
            released_at__isnull=True,
            is_sms_enabled=True,
        ).exclude(
            phone_number__in=sms_in_use,
        ).values_list('id', flat=True)
    )

    if not unused_pks:
        logger.warning("No unused SMS numbers available.")
        raise ValueError("No unused SMS numbers available.")

    random_pk = random.choice(unused_pks)
    return SmsNumber.objects.get(pk=random_pk)

@tracer.start_as_current_span("SMS get_user_primary_sms_number")
def get_user_primary_sms_number(user) -> Optional[UserPhoneNumber]:
    """
    Get the user's primary SMS number, if it exists.
    Returns None if no primary number is set.
    """
    span = trace.get_current_span()
    try:
        phone_number = UserPhoneNumber.objects.filter(
            user=user,
            is_primary=True,
            is_verified=True,  # Ensure the number is verified
        ).first()

    except UserPhoneNumber.DoesNotExist:
        return None
    except Exception as e:
        logger.error(f"Error retrieving primary SMS number for user {user.id}: {e}")
        span.record_exception(e)
        span.add_event("Error retrieving primary SMS number", {"error": str(e)})
        return None

    return phone_number


def _normalize_us_phone_number(phone_number: str) -> Optional[str]:
    try:
        from phonenumbers import (
            NumberParseException,
            PhoneNumberFormat,
            format_number,
            is_valid_number,
            parse,
            region_code_for_number,
        )
    except ImportError:
        logger.warning("phonenumbers is unavailable; leaving Twilio riskCheck enabled.")
        return None

    try:
        parsed = parse((phone_number or "").strip(), None)
    except NumberParseException:
        return None

    if not is_valid_number(parsed) or region_code_for_number(parsed) != "US":
        return None
    return format_number(parsed, PhoneNumberFormat.E164)


def should_disable_twilio_risk_check(to_number: str, owner_user=None) -> bool:
    """
    Disable Twilio riskCheck only for verified US phone numbers registered to
    the agent owner's account. Our customer-care A2P campaign is exempt for
    this first-party owner-contact path.
    """
    owner_user_id = getattr(owner_user, "id", None)
    if not owner_user_id:
        return False
    normalized_to = _normalize_us_phone_number(to_number)
    if not normalized_to:
        return False
    return UserPhoneNumber.objects.filter(
        user_id=owner_user_id,
        phone_number__iexact=normalized_to,
        is_verified=True,
    ).exists()


@tracer.start_as_current_span("SMS send_sms")
def send_sms(to_number: str, from_number: str, body: str, *, owner_user=None) -> bool|str:
    """
    Send an SMS message using Twilio.
    Returns True if sent successfully, False otherwise.
    """
    client = _get_client()
    span = trace.get_current_span()

    if not client:
        logger.warning("Twilio client not configured; cannot send SMS.")
        return False

    optimization = optimize_sms_for_cost(body, max_length=settings.SMS_MAX_BODY_LENGTH)
    if optimization["changed"]:
        logger.info(
            "Normalized SMS body before Twilio send: encoding %s -> %s, segments %d -> %d.",
            optimization["original_encoding"],
            optimization["final_encoding"],
            optimization["original_segments"],
            optimization["final_segments"],
        )
        span.set_attribute("sms.normalized_for_gsm7", True)
        span.set_attribute("sms.original_encoding", optimization["original_encoding"])
        span.set_attribute("sms.final_encoding", optimization["final_encoding"])
        span.set_attribute("sms.segments_saved", optimization["segments_saved"])
    else:
        span.set_attribute("sms.normalized_for_gsm7", False)
    body = optimization["text"]
    if len(body) > settings.SMS_MAX_BODY_LENGTH:
        logger.warning(
            "SMS body exceeds maximum length of %d characters after normalization.",
            settings.SMS_MAX_BODY_LENGTH,
        )
        return False

    try:
        with traced("SMS send_sms - Twilio"):
            logger.info(f"Sending SMS to {to_number} from {from_number}: {body}")
            message_kwargs = {
                "body": body,
                "from_": from_number,
                "to": to_number,
                "messaging_service_sid": settings.TWILIO_MESSAGING_SERVICE_SID,
            }
            if should_disable_twilio_risk_check(to_number, owner_user=owner_user):
                message_kwargs["risk_check"] = TWILIO_RISK_CHECK_DISABLE
            message = client.messages.create(**message_kwargs)

        logger.info(f"SMS sent successfully to {to_number}: {message.sid}")
        return message.sid

    except Exception as e:
        logger.error(f"Failed to send SMS to {to_number}: {e}")
        span.record_exception(e)
        span.add_event("SMS send failed", {"error": str(e)})
        return False

@tracer.start_as_current_span("SMS twilio_find_numbers")
def sms_twilio_find_numbers(
    country: str,
    area_code: Optional[str] = None,
    vanity: Optional[str] = None,
    count: int = 1,
    sms_only: bool = False
) -> list:
    """
    Find available Twilio phone numbers based on criteria.
    Returns a list of available phone numbers.
    """
    span = trace.get_current_span()
    span.set_attribute("country", country)
    span.set_attribute("area_code", area_code)
    span.set_attribute("vanity", vanity)
    span.set_attribute("sms_only", sms_only)

    client = _get_client()
    if not client:
        logger.warning("Twilio client not configured; cannot find numbers.")
        return []

    filters = {
        "sms_enabled": sms_only,
        "area_code": area_code,
        "contains": vanity.upper() if vanity else None,
        "limit": max(100, count * 5),
    }

    # Remove None values from filters
    filters = {k: v for k, v in filters.items() if v is not None}

    search = client.available_phone_numbers(country).local
    candidates = search.list(**filters)

    span.set_attribute("candidates.count", len(candidates))

    if vanity:
        candidates = [n for n in candidates if ends_with_vanity(n.phone_number, vanity)]

    span.set_attribute("candidates.matching_count", len(candidates))

    return candidates

@tracer.start_as_current_span("SMS twilio_purchase_numbers")
def sms_twilio_purchase_numbers(number: str) -> bool:
    """
    Purchase Twilio phone numbers based on criteria.
    Returns a list of purchased phone numbers.
    """
    span = trace.get_current_span()
    span.set_attribute("phone.number", number)
    client = _get_client()
    if not client:
        logger.warning("Twilio client not configured; cannot purchase numbers.")
        return False

    try:
        incoming = client.incoming_phone_numbers.create(
            phone_number=number
        )
        client.messaging.services(settings.TWILIO_MESSAGING_SERVICE_SID).phone_numbers.create(
            phone_number_sid=incoming.sid
        )

        span.add_event('SMS number purchased', {'phone.number': number})
        logger.info(f"SMS number purchased successfully: {number}")
    except TwilioRestException as e:
        logger.error(f"Failed to purchase number {number}: {e}")
        span.add_event('SMS purchase failed', {'error': str(e)})
        span.record_exception(e)
        return False

    return True
