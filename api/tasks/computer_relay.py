from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from api.models import (
    ComputerDeviceCredential,
    ComputerPairingSession,
    ComputerRelayArtifact,
)


@shared_task(ignore_result=True)
def cleanup_computer_relay_records() -> None:
    now = timezone.now()
    (
        ComputerRelayArtifact.objects.filter(expires_at__lte=now)
        | ComputerRelayArtifact.objects.filter(
            consumed_at__isnull=False,
            consumed_at__lte=now - timedelta(hours=1),
        )
    ).delete()

    ComputerPairingSession.objects.filter(
        expires_at__lte=now,
    ).delete()
    ComputerDeviceCredential.objects.filter(
        expires_at__lt=now,
    ).delete()
