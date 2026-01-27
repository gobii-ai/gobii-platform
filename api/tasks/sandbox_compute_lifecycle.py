import logging
from typing import Any, Dict

from celery import shared_task

from api.services.sandbox_compute import sandbox_compute_enabled
from api.services.sandbox_compute_lifecycle import SandboxComputeScheduler

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.sandbox_compute.sweep_idle_sessions")
def sweep_idle_sandbox_sessions(limit: int = 100) -> Dict[str, Any]:
    if not sandbox_compute_enabled():
        return {"status": "skipped", "message": "Sandbox compute disabled"}
    scheduler = SandboxComputeScheduler()
    return scheduler.sweep_idle_sessions(limit=limit)
