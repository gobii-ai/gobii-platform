from datetime import timedelta

from celery import shared_task
from django.core.files.storage import default_storage
from django.utils import timezone

from api.models import (
    ComputerDeviceCredential,
    ComputerPairingSession,
    ComputerRelayArtifact,
)


@shared_task(ignore_result=True)
def cleanup_computer_relay_records() -> None:
    now = timezone.now()
    artifacts = list(
        ComputerRelayArtifact.objects.filter(expires_at__lte=now)
        | ComputerRelayArtifact.objects.filter(
            consumed_at__isnull=False,
            consumed_at__lte=now - timedelta(hours=1),
        )
    )
    for artifact in artifacts:
        default_storage.delete(artifact.storage_key)
    if artifacts:
        ComputerRelayArtifact.objects.filter(id__in=[artifact.id for artifact in artifacts]).delete()

    ComputerPairingSession.objects.filter(
        expires_at__lte=now,
    ).delete()
    ComputerDeviceCredential.objects.filter(
        expires_at__lt=now,
    ).delete()
