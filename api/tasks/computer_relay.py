from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from api.models import (
    ComputerDeviceApp,
    ComputerDeviceAssignment,
    ComputerDeviceCredential,
    ComputerPairingSession,
    ComputerRelayArtifact,
)


@shared_task(ignore_result=True)
def enable_computer_tools(device_id: str) -> dict:
    from api.agent.tasks.process_events import process_agent_events_task
    from api.agent.tools.mcp_manager import get_mcp_manager
    from api.agent.tools.tool_manager import enable_tools
    from api.services.computer_relay import computer_cpp_enabled_for_user

    assignment = (
        ComputerDeviceAssignment.objects.select_related("agent", "device__owner")
        .filter(device_id=device_id, revoked_at__isnull=True, device__revoked_at__isnull=True)
        .first()
    )
    if assignment is None or not computer_cpp_enabled_for_user(assignment.device.owner):
        return {"status": "skipped", "message": "Computer assignment is unavailable"}

    apps = ComputerDeviceApp.objects.filter(
        device_id=device_id,
        approval_state=ComputerDeviceApp.ApprovalState.APPROVED,
        is_available=True,
        mcp_server_config__is_active=True,
    )
    config_ids = {
        str(app.mcp_server_config_id)
        for app in apps
        if app.approved_schema_hash == app.reported_schema_hash
    }
    manager = get_mcp_manager()
    tools = manager.get_tools_for_agent(assignment.agent, allowed_config_ids=config_ids)

    if not tools:
        return {"status": "skipped", "message": "No approved computer tools were discovered"}
    result = enable_tools(assignment.agent, [tool.full_name for tool in tools])
    if result["enabled"]:
        process_agent_events_task.delay(str(assignment.agent_id))
    return result


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
