import logging
from typing import Any, Dict, Optional

from django.db import DatabaseError, transaction
from django.utils import timezone

from api.models import AgentComputeSession
from api.services.sandbox_compute import SandboxComputeService, SandboxComputeUnavailable, sandbox_compute_enabled

logger = logging.getLogger(__name__)


class SandboxComputeScheduler:
    def __init__(self, service: Optional[SandboxComputeService] = None) -> None:
        if not sandbox_compute_enabled():
            raise SandboxComputeUnavailable("Sandbox compute is disabled.")
        self._service = service or SandboxComputeService()

    def sweep_idle_sessions(self, *, limit: int = 100) -> Dict[str, Any]:
        now = timezone.now()
        candidates = (
            AgentComputeSession.objects.filter(
                state=AgentComputeSession.State.RUNNING,
                lease_expires_at__lte=now,
            )
            .order_by("lease_expires_at")
            .values_list("agent_id", flat=True)[:limit]
        )

        scanned = 0
        stopped = 0
        skipped = 0
        errors = 0

        for agent_id in candidates:
            scanned += 1
            try:
                with transaction.atomic():
                    session = (
                        AgentComputeSession.objects.select_for_update(skip_locked=True)
                        .select_related("agent")
                        .filter(agent_id=agent_id)
                        .first()
                    )
                    if not session:
                        skipped += 1
                        continue
                    if session.state != AgentComputeSession.State.RUNNING:
                        skipped += 1
                        continue
                    if session.lease_expires_at and session.lease_expires_at > now:
                        skipped += 1
                        continue
                    session.state = AgentComputeSession.State.IDLE_STOPPING
                    session.save(update_fields=["state", "updated_at"])
            except DatabaseError:
                logger.exception("Failed to lock sandbox session for agent=%s", agent_id)
                errors += 1
                continue

            try:
                result = self._service.idle_stop_session(session, reason="idle_ttl")
                if result.get("status") == "ok":
                    stopped += 1
                else:
                    errors += 1
            except SandboxComputeUnavailable as exc:
                logger.warning("Sandbox idle stop unavailable for agent=%s: %s", agent_id, exc)
                errors += 1
            except (RuntimeError, ValueError) as exc:
                logger.warning("Sandbox idle stop failed for agent=%s: %s", agent_id, exc)
                errors += 1

        return {
            "status": "ok",
            "scanned": scanned,
            "stopped": stopped,
            "skipped": skipped,
            "errors": errors,
        }
