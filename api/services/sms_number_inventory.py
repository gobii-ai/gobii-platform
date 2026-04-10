import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.models import CommsChannel, PersistentAgentCommsEndpoint, SmsNumber, SmsProvider
from util.integrations import twilio_status

try:
    from twilio.base.exceptions import TwilioRestException
    from twilio.rest import Client
except ImportError:  # pragma: no cover - optional dependency in some environments
    class TwilioRestException(Exception):
        pass

    Client = None

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsNumberReleaseResult:
    phone_number: str
    detached_endpoint_count: int
    retired_locally: bool
    twilio_released: bool
    twilio_message: str = ""
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.error


def sms_number_is_in_use(sms_number: SmsNumber) -> bool:
    return PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.SMS,
        address__iexact=sms_number.phone_number,
        owner_agent__isnull=False,
    ).exists()


def retire_sms_number(sms_number: SmsNumber) -> bool:
    """
    Retire a number locally so it remains in history but is never allocated again.
    """
    if sms_number_is_in_use(sms_number):
        raise ValidationError(
            {"phone_number": "Cannot retire an SMS number while it is still assigned to an SMS endpoint."}
        )

    update_fields = []
    if sms_number.is_active:
        sms_number.is_active = False
        update_fields.append("is_active")
    if sms_number.released_at is None:
        sms_number.released_at = timezone.now()
        update_fields.append("released_at")

    if update_fields:
        sms_number.save(update_fields=update_fields)
        return True

    return False


def _get_twilio_release_client() -> Client:
    status = twilio_status()
    if not status.enabled:
        raise ValidationError(
            {"phone_number": status.reason or "Twilio integration is disabled, so numbers cannot be released."}
        )
    if Client is None:
        raise ValidationError({"phone_number": "Twilio SDK is unavailable, so numbers cannot be released."})
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise ValidationError({"phone_number": "Twilio credentials are missing, so numbers cannot be released."})
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _remove_twilio_messaging_service_binding(client: Client, sms_number: SmsNumber) -> None:
    service_sid = sms_number.messaging_service_sid or settings.TWILIO_MESSAGING_SERVICE_SID
    if not service_sid:
        return

    try:
        client.messaging.v1.services(service_sid).phone_numbers(sms_number.sid).delete()
    except TwilioRestException as exc:
        if exc.status == 404:
            logger.info(
                "SMS number %s was already absent from Twilio messaging service %s",
                sms_number.phone_number,
                service_sid,
            )
            return
        raise


def _release_twilio_incoming_number(client: Client, sms_number: SmsNumber) -> str:
    try:
        released = client.incoming_phone_numbers(sms_number.sid).delete()
    except TwilioRestException as exc:
        if exc.status == 404:
            logger.info("SMS number %s was already absent in Twilio", sms_number.phone_number)
            return "already absent in Twilio"
        raise

    if not released:
        raise ValidationError(
            {"phone_number": f"Twilio did not confirm release of {sms_number.phone_number}."}
        )

    return "released in Twilio"


def release_sms_number(sms_number: SmsNumber) -> SmsNumberReleaseResult:
    """
    Fully release a Twilio inventory number while preserving endpoint and message history.
    """
    if sms_number.provider != SmsProvider.TWILIO:
        raise ValidationError({"phone_number": f"{sms_number.phone_number} is not a Twilio inventory number."})

    client = _get_twilio_release_client()

    with transaction.atomic():
        locked_sms_number = SmsNumber.objects.select_for_update().get(pk=sms_number.pk)
        detached_endpoint_count = PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.SMS,
            address__iexact=locked_sms_number.phone_number,
            owner_agent__isnull=False,
        ).update(owner_agent=None)
        retired_locally = retire_sms_number(locked_sms_number)

    try:
        _remove_twilio_messaging_service_binding(client, locked_sms_number)
        twilio_message = _release_twilio_incoming_number(client, locked_sms_number)
    except TwilioRestException as exc:
        logger.warning(
            "Released SMS number %s locally but failed to release it in Twilio: %s",
            locked_sms_number.phone_number,
            exc,
        )
        return SmsNumberReleaseResult(
            phone_number=locked_sms_number.phone_number,
            detached_endpoint_count=detached_endpoint_count,
            retired_locally=retired_locally,
            twilio_released=False,
            error=str(exc),
        )
    except ValidationError as exc:
        logger.warning(
            "Released SMS number %s locally but Twilio did not confirm release: %s",
            locked_sms_number.phone_number,
            exc,
        )
        return SmsNumberReleaseResult(
            phone_number=locked_sms_number.phone_number,
            detached_endpoint_count=detached_endpoint_count,
            retired_locally=retired_locally,
            twilio_released=False,
            error="; ".join(exc.messages),
        )

    return SmsNumberReleaseResult(
        phone_number=locked_sms_number.phone_number,
        detached_endpoint_count=detached_endpoint_count,
        retired_locally=retired_locally,
        twilio_released=True,
        twilio_message=twilio_message,
    )
