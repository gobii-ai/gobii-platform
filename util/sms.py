from __future__ import annotations

import logging
import random
from typing import Optional
from opentelemetry import trace
from api.models import SmsNumber, PersistentAgentCommsEndpoint, CommsChannel, UserPhoneNumber
from config import settings
from config.settings import TWILIO_MESSAGING_SERVICE_SID
from observability import traced
from twilio.base.exceptions import TwilioRestException

try:
    from twilio.rest import Client
except Exception:  # pragma: no cover - dependency optional in tests
    Client = None  # type: ignore

logger = logging.getLogger(__name__)

tracer = trace.get_tracer("gobii.utils")

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
    account_sid = settings.TWILIO_ACCOUNT_SID
    auth_token = settings.TWILIO_AUTH_TOKEN
    if not account_sid or not auth_token or Client is None:
        return None
    return Client(account_sid, auth_token)

@tracer.start_as_current_span("SMS start_verification")
def start_verification(phone_number: str) -> Optional[str]:
    """Start an SMS verification; returns verification SID if sent."""
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
    # Look up Persistent Agent SMS Comm Endpoints, owned by a Persistent Agent owned by the user. Then find a number
    # that is not in use, and return that. If no such number exists, raise an exception.
    sms_in_use = PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.SMS,
    ).values_list('address', flat=True)

    unused_pks = list(SmsNumber.objects.exclude(
        phone_number__in=sms_in_use,
    ).values_list('id', flat=True))

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

@tracer.start_as_current_span("SMS send_sms")
def send_sms(to_number: str, from_number: str, body: str) -> bool|str:
    """
    Send an SMS message using Twilio.
    Returns True if sent successfully, False otherwise.
    """
    client = _get_client()
    span = trace.get_current_span()

    if not client:
        logger.warning("Twilio client not configured; cannot send SMS.")
        return False

    try:
        with traced("SMS send_sms - Twilio"):
            logger.info(f"Sending SMS to {to_number} from {from_number}: {body}")
            message = client.messages.create(
                body=body,
                from_=from_number,
                to=to_number,
                messaging_service_sid=TWILIO_MESSAGING_SERVICE_SID,
            )

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
        client.messaging.services(TWILIO_MESSAGING_SERVICE_SID).phone_numbers.create(
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