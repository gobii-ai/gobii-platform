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


def _cleanup_pipedream_delete_account(agent_id: str, reason: str, meta: Optional[dict]) -> None:
    """Delete the Pipedream Connect account for this external user (agent).

    Uses the Pipedream Connect API `DELETE /v1/connect/{project_id}/accounts/{external_user_id}`.
    External user id is the agent ID. This is safe to call multiple times; a 404
    (account not found) is treated as success for cleanup purposes.
    """
    try:
        from django.conf import settings
        from api.agent.tools.mcp_manager import get_mcp_manager
        import requests

        project_id = getattr(settings, "PIPEDREAM_PROJECT_ID", "")
        environment = getattr(settings, "PIPEDREAM_ENVIRONMENT", "development")
        if not project_id:
            logger.info("Pipedream cleanup skipped (no project id). agent=%s", agent_id)
            return

        mgr = get_mcp_manager()
        token = mgr._get_pipedream_access_token() or ""
        if not token:
            logger.info("Pipedream cleanup skipped (no access token). agent=%s", agent_id)
            return

        url = f"https://api.pipedream.com/v1/connect/{project_id}/accounts/{agent_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "x-pd-environment": environment,
        }

        resp = requests.delete(url, headers=headers, timeout=20)
        if resp.status_code in (200, 202, 204):
            logger.info("Pipedream account deleted agent=%s reason=%s", agent_id, reason)
            return
        if resp.status_code == 404:
            logger.info("Pipedream account already absent agent=%s reason=%s", agent_id, reason)
            return
        try:
            resp.raise_for_status()
        except Exception:
            logger.exception(
                "Pipedream account delete failed agent=%s reason=%s status=%s body=%s",
                agent_id, reason, resp.status_code, resp.text[:500]
            )
    except Exception:
        logger.exception("Pipedream account cleanup error for agent %s", agent_id)


# Register after definition so it runs after sessions cleanup. Limit to more
# final shutdowns to avoid removing accounts on transient pauses.
AgentCleanupRegistry.register(
    _cleanup_pipedream_delete_account,
    reasons=[AgentShutdownReason.HARD_DELETE, AgentShutdownReason.SOFT_EXPIRE],
)
