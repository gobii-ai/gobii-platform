from datetime import timedelta

from celery import shared_task
from django.core.files.storage import default_storage
from django.utils import timezone

from api.models import (
    ComputerDeviceAssignment,
    ComputerDeviceCredential,
    ComputerPairingSession,
    ComputerRelayArtifact,
    OrganizationMembership,
)
from api.services.computer_relay import revoke_assignment
from api.services.organization_permissions import ORG_AGENT_CONFIG_AUTHORITY_ROLES


@shared_task(ignore_result=True)
def cleanup_computer_relay_records() -> dict[str, int]:
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
    artifact_ids = [artifact.id for artifact in artifacts]
    if artifact_ids:
        ComputerRelayArtifact.objects.filter(id__in=artifact_ids).delete()

    pairings_deleted, _ = ComputerPairingSession.objects.filter(
        expires_at__lte=now,
    ).delete()
    credentials_deleted, _ = ComputerDeviceCredential.objects.filter(
        expires_at__lt=now,
    ).delete()

    revoked_assignments = 0
    assignments = ComputerDeviceAssignment.objects.filter(
        status=ComputerDeviceAssignment.Status.ACTIVE,
        organization__isnull=False,
    ).select_related("device__owner", "organization")
    for assignment in assignments:
        still_authorized = OrganizationMembership.objects.filter(
            user=assignment.device.owner,
            org=assignment.organization,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=ORG_AGENT_CONFIG_AUTHORITY_ROLES,
        ).exists()
        if not still_authorized:
            revoke_assignment(assignment.device)
            revoked_assignments += 1

    return {
        "artifacts_deleted": len(artifact_ids),
        "pairing_rows_deleted": pairings_deleted,
        "credential_rows_deleted": credentials_deleted,
        "assignments_revoked": revoked_assignments,
    }
