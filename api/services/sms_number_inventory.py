from django.core.exceptions import ValidationError
from django.utils import timezone

from api.models import CommsChannel, PersistentAgentCommsEndpoint, SmsNumber


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
