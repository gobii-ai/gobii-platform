import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from api.models import AgentComputeSession, PersistentAgent

logger = logging.getLogger(__name__)


def get_or_create_session(agent: PersistentAgent, *, namespace: str) -> AgentComputeSession:
    if not namespace:
        raise ValueError("namespace is required to create a compute session")

    with transaction.atomic():
        session, created = AgentComputeSession.objects.select_for_update().get_or_create(
            agent=agent,
            defaults={
                "namespace": namespace,
            },
        )
        if not created and session.namespace != namespace:
            session.namespace = namespace
            session.save(update_fields=["namespace", "updated_at"])
        return session


def touch_session(session: AgentComputeSession, *, now: Optional[timezone.datetime] = None) -> None:
    stamp = now or timezone.now()
    session.last_activity_at = stamp
    session.save(update_fields=["last_activity_at", "updated_at"])


def mark_session_running(
    session: AgentComputeSession,
    *,
    pod_name: str,
    workspace_pvc: str,
    now: Optional[timezone.datetime] = None,
) -> None:
    stamp = now or timezone.now()
    session.pod_name = pod_name
    session.workspace_pvc = workspace_pvc
    session.state = AgentComputeSession.State.RUNNING
    session.last_activity_at = stamp
    session.last_error = ""
    session.save(update_fields=[
        "pod_name",
        "workspace_pvc",
        "state",
        "last_activity_at",
        "last_error",
        "updated_at",
    ])


def mark_session_idle_stopping(
    session: AgentComputeSession,
    *,
    now: Optional[timezone.datetime] = None,
) -> None:
    stamp = now or timezone.now()
    session.state = AgentComputeSession.State.IDLE_STOPPING
    session.last_activity_at = stamp
    session.save(update_fields=["state", "last_activity_at", "updated_at"])


def mark_session_stopped(
    session: AgentComputeSession,
    *,
    error_message: str | None = None,
) -> None:
    session.state = AgentComputeSession.State.STOPPED
    if error_message:
        session.last_error = error_message
    session.pod_name = ""
    session.save(update_fields=["state", "last_error", "pod_name", "updated_at"])


def mark_session_error(
    session: AgentComputeSession,
    *,
    error_message: str,
) -> None:
    session.state = AgentComputeSession.State.ERROR
    session.last_error = (error_message or "")[:2000]
    session.save(update_fields=["state", "last_error", "updated_at"])
