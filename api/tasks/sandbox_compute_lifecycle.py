import logging
from typing import Any, Dict

from celery import shared_task
from django.conf import settings

from api.services.sandbox_compute import sandbox_compute_enabled
from api.services.sandbox_compute_lifecycle import SandboxComputeScheduler

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.sandbox_compute.sweep_idle_sessions")
def sweep_idle_sandbox_sessions(limit: int = 100) -> Dict[str, Any]:
    if not sandbox_compute_enabled():
        return {"status": "skipped", "message": "Sandbox compute disabled"}
    scheduler = SandboxComputeScheduler()
    result = scheduler.sweep_idle_sessions(limit=limit)
    if settings.SANDBOX_COMPUTE_RECONCILE_TERMINAL_SESSIONS:
        result["terminal_reconcile"] = scheduler.reconcile_terminal_sessions(
            limit=settings.SANDBOX_COMPUTE_TERMINAL_RECONCILE_LIMIT,
            grace_seconds=settings.SANDBOX_COMPUTE_TERMINAL_RECONCILE_GRACE_SECONDS,
            delete_workspaces=settings.SANDBOX_COMPUTE_RECONCILE_DELETE_WORKSPACES,
            delete_snapshots=settings.SANDBOX_COMPUTE_RECONCILE_DELETE_SNAPSHOTS,
        )
    return result
