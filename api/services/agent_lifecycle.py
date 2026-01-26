import logging
from typing import Callable, Dict, List, Optional, Set, Tuple

from django.db import transaction


logger = logging.getLogger(__name__)


class AgentShutdownReason:
    HARD_DELETE = "HARD_DELETE"
    PAUSE = "PAUSE"
    CRON_DISABLED = "CRON_DISABLED"
    SOFT_EXPIRE = "SOFT_EXPIRE"


# Handler signature: handler(agent_id: str, reason: str, meta: Optional[dict]) -> None
CleanupHandler = Callable[[str, str, Optional[dict]], None]


class AgentCleanupRegistry:
    """Simple in‑process registry for agent cleanup handlers.

    Handlers MUST be idempotent. They may be called multiple times for the same
    (agent_id, reason). Handlers should log and swallow their own errors so that
    one failure does not prevent subsequent handlers from running.
    """

    # Store (handler, allowed_reasons or None for all)
    _handlers: List[Tuple[CleanupHandler, Optional[Set[str]]]] = []

    @classmethod
    def register(cls, handler: CleanupHandler, *, reasons: Optional[List[str]] = None) -> None:
        allowed: Optional[Set[str]] = set(reasons) if reasons else None
        # Avoid duplicate entries
        for h, r in cls._handlers:
            if h is handler and (r == allowed or (r is None and allowed is None)):
                return
        cls._handlers.append((handler, allowed))

    @classmethod
    def get_for_reason(cls, reason: str) -> List[CleanupHandler]:
        res: List[CleanupHandler] = []
        for handler, allowed in cls._handlers:
            if allowed is None or reason in allowed:
                res.append(handler)
        return res


class AgentLifecycleService:
    """One‑stop entry point to initiate agent shutdown cleanups.

    Use this when an agent is deleted, paused, disabled (no schedule), or
    soft‑expired. Schedules a Celery task after the surrounding transaction
    commits to perform heavy work out of band.
    """

    @staticmethod
    def shutdown(agent_id: str, reason: str, meta: Optional[Dict] = None) -> None:
        try:
            # Defer actual work until after DB commit to avoid running against
            # uncommitted state or rolling back side effects on failure.
            def _enqueue():
                try:
                    from api.tasks.agent_lifecycle import agent_shutdown_cleanup_task

                    agent_shutdown_cleanup_task.delay(str(agent_id), str(reason), meta or {})
                except Exception:
                    logger.exception("Failed to enqueue agent shutdown cleanup task for %s", agent_id)

            transaction.on_commit(_enqueue)
        except Exception:
            logger.exception("Failed to schedule agent shutdown cleanup for %s", agent_id)


# ---- Built‑in handler examples (lightweight, idempotent) -------------------

def _cleanup_pipedream_sessions(agent_id: str, reason: str, meta: Optional[dict]) -> None:
    """Mark any pending Pipedream Connect sessions as errored.

    This is safe and idempotent. For hard‑delete, sessions may already be
    cascaded away; the update simply affects 0 rows.
    """
    try:
        from api.models import PipedreamConnectSession

        updated = (
            PipedreamConnectSession.objects
            .filter(agent_id=agent_id, status=PipedreamConnectSession.Status.PENDING)
            .update(status=PipedreamConnectSession.Status.ERROR)
        )
        if updated:
            logger.info("Pipedream sessions cleanup: agent=%s reason=%s updated=%d", agent_id, reason, updated)
    except Exception:
        logger.exception("Pipedream sessions cleanup failed for agent %s", agent_id)


# Register default handlers
AgentCleanupRegistry.register(_cleanup_pipedream_sessions)  # all reasons


def _cleanup_pipedream_delete_user(agent_id: str, reason: str, meta: Optional[dict]) -> None:
    """Delete the Pipedream Connect external user (by agent_id) via shared helper.

    Uses `api.integrations.pipedream_connect_gc.delete_external_user`, which already
    handles auth, environment, retries, and idempotent semantics (204/404 as success).
    """
    try:
        from api.integrations.pipedream_connect_gc import delete_external_user

        ok, status, msg = delete_external_user(str(agent_id))
        if ok:
            logger.info("Pipedream external user deleted agent=%s reason=%s status=%s", agent_id, reason, status)
        else:
            logger.warning("Pipedream external user delete failed agent=%s status=%s msg=%s", agent_id, status, (msg or ""))
    except Exception:
        logger.exception("Pipedream external user cleanup error for agent %s", agent_id)


# Register after definition so it runs after sessions cleanup. Limit to more
# final shutdowns to avoid removing accounts on transient pauses.
AgentCleanupRegistry.register(
    _cleanup_pipedream_delete_user,
    reasons=[AgentShutdownReason.HARD_DELETE, AgentShutdownReason.SOFT_EXPIRE],
)


def _cleanup_compute_session(agent_id: str, reason: str, meta: Optional[dict]) -> None:
    """Terminate any running sandbox compute pod for the agent."""
    try:
        from api.models import PersistentAgent
        from api.services.compute_control import terminate

        agent = PersistentAgent.objects.filter(id=agent_id).first()
        if agent is None:
            return
        terminate(agent, reason=f"Agent shutdown: {reason}")
    except Exception:
        logger.exception("Sandbox compute cleanup failed for agent %s", agent_id)


AgentCleanupRegistry.register(_cleanup_compute_session)
