import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from twilio.rest import Client
from api.models import SmsNumber
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


@shared_task
def sync_twilio_numbers():
    """
    Pull phone-number metadata from Twilio’s Messaging Service
    and reconcile it with the SmsNumber table.
    """
    client = Client(settings.TWILIO_ACCOUNT_SID,
                    settings.TWILIO_AUTH_TOKEN)

    service_sid = settings.TWILIO_MESSAGING_SERVICE_SID

    # ─────────── Pull once from Twilio ───────────
    remote = {
        pn.sid: pn
        for pn in client.messaging \
                       .services(service_sid) \
                       .phone_numbers \
                       .list(limit=1000)   # hard cap is 400, but be safe
    }

    # ─────────── Upsert or update ───────────
    for sid, pn in remote.items():
        SmsNumber.objects.update_or_create(
            sid=sid,
            defaults={
                "phone_number": pn.phone_number,
                "friendly_name": getattr(pn, "friendly_name", ""),
                "country": getattr(pn, "country_code", ""),   # API gives `country_code` :contentReference[oaicite:1]{index=1}
                "region": getattr(pn, "region", ""),
                "is_sms_enabled": "SMS" in pn.capabilities,
                "is_mms_enabled": "MMS" in pn.capabilities,
                "is_active": True,
                "extra": {},        # TODO: Store the full Twilio phone number object
                "last_synced_at": timezone.now(),
                "messaging_service_sid": service_sid,
            },
        )

    # ─────────── Deactivate missing numbers ───────────
    SmsNumber.objects.filter(
        is_active=True
    ).exclude(sid__in=remote.keys()).update(is_active=False)


@shared_task
def send_test_sms(sms_number_id: int, to: str, body: str):
    sms_number = SmsNumber.objects.get(pk=sms_number_id)

    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    client.messages.create(
        from_=sms_number.phone_number,
        to=to,
        body=body,
        messaging_service_sid=sms_number.messaging_service_sid or None,
    )