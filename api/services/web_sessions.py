from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from api.models import PersistentAgent, PersistentAgentWebSession

from django.conf import settings

WEB_SESSION_TTL_SECONDS = 60
WEB_SESSION_RETENTION_DAYS = getattr(settings, "WEB_SESSION_RETENTION_DAYS", 30)
WEB_SESSION_STALE_GRACE_MINUTES = getattr(settings, "WEB_SESSION_STALE_GRACE_MINUTES", 120)
_HEARTBEAT_SOURCE = "heartbeat"
_START_SOURCE = "start"
_END_SOURCE = "end"
_MESSAGE_SOURCE = "message"
_SSE_SOURCE = "sse"


def _now():
    return timezone.now()


def _deadline(session: PersistentAgentWebSession, *, ttl_seconds: int) -> timezone.datetime:
    return session.last_seen_at + timedelta(seconds=ttl_seconds)


def _is_session_live(
    session: PersistentAgentWebSession,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    now: Optional[timezone.datetime] = None,
) -> bool:
    reference = now or _now()
    if session.ended_at is not None:
        return False
    return reference <= _deadline(session, ttl_seconds=ttl_seconds)


def _mark_session_ended(
    session: PersistentAgentWebSession,
    *,
    ended_at: Optional[timezone.datetime] = None,
    source: str | None = None,
) -> PersistentAgentWebSession:
    timestamp = ended_at or _now()
    if session.ended_at is None:
        session.ended_at = timestamp
        if source:
            session.last_seen_source = source[:32]
        session.save(update_fields=["ended_at", "last_seen_source"])
    return session


def _touch_session(
    session: PersistentAgentWebSession,
    *,
    now: timezone.datetime,
    source: str | None,
) -> PersistentAgentWebSession:
    session.last_seen_at = now
    if source:
        session.last_seen_source = source[:32]
    session.ended_at = None
    session.save(update_fields=["last_seen_at", "last_seen_source", "ended_at"])
    return session


@dataclass(frozen=True)
class SessionResult:
    session: PersistentAgentWebSession
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS

    @property
    def expires_at(self):
        return _deadline(self.session, ttl_seconds=self.ttl_seconds)


def start_web_session(
    agent: PersistentAgent,
    user,
    *,
    source: str | None = None,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> SessionResult:
    stamp = _now()
    with transaction.atomic():
        session, created = (
            PersistentAgentWebSession.objects.select_for_update()
            .get_or_create(
                agent=agent,
                user=user,
                defaults={
                    "session_key": uuid.uuid4(),
                    "started_at": stamp,
                    "last_seen_at": stamp,
                    "last_seen_source": (source or _START_SOURCE)[:32],
                },
            )
        )

        if not created:
            session.session_key = uuid.uuid4()
            session.started_at = stamp
            session.last_seen_at = stamp
            session.last_seen_source = (source or _START_SOURCE)[:32]
            session.ended_at = None
            session.save(
                update_fields=[
                    "session_key",
                    "started_at",
                    "last_seen_at",
                    "last_seen_source",
                    "ended_at",
                ]
            )

    return SessionResult(session=session, ttl_seconds=ttl_seconds)


def heartbeat_web_session(
    *,
    session_key: uuid.UUID | str,
    agent: PersistentAgent,
    user,
    source: str | None = None,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> SessionResult:
    stamp = _now()
    key = uuid.UUID(str(session_key))
    with transaction.atomic():
        try:
            session = (
                PersistentAgentWebSession.objects.select_for_update()
                .get(session_key=key)
            )
        except PersistentAgentWebSession.DoesNotExist as exc:
            raise ValueError("Unknown web session.") from exc

        if session.agent_id != agent.id or session.user_id != getattr(user, "id", None):
            raise ValueError("Session does not belong to this agent or user.")

        if not _is_session_live(session, ttl_seconds=ttl_seconds, now=stamp):
            _mark_session_ended(session, ended_at=stamp, source=source or _HEARTBEAT_SOURCE)
            raise ValueError("Web session has expired.")

        _touch_session(session, now=stamp, source=source or _HEARTBEAT_SOURCE)

    return SessionResult(session=session, ttl_seconds=ttl_seconds)


def end_web_session(
    *,
    session_key: uuid.UUID | str,
    agent: PersistentAgent,
    user,
    source: str | None = None,
) -> SessionResult:
    key = uuid.UUID(str(session_key))
    with transaction.atomic():
        try:
            session = (
                PersistentAgentWebSession.objects.select_for_update()
                .get(session_key=key)
            )
        except PersistentAgentWebSession.DoesNotExist as exc:
            raise ValueError("Unknown web session.") from exc

        if session.agent_id != agent.id or session.user_id != getattr(user, "id", None):
            raise ValueError("Session does not belong to this agent or user.")

        _mark_session_ended(session, source=source or _END_SOURCE)

    return SessionResult(session=session)


def touch_web_session(
    agent: PersistentAgent,
    user,
    *,
    source: str | None = None,
    create: bool = True,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> Optional[SessionResult]:
    stamp = _now()
    with transaction.atomic():
        try:
            session = (
                PersistentAgentWebSession.objects.select_for_update()
                .get(agent=agent, user=user)
            )
        except PersistentAgentWebSession.DoesNotExist:
            if not create:
                return None
            return start_web_session(agent, user, source=source or _MESSAGE_SOURCE, ttl_seconds=ttl_seconds)

        if not _is_session_live(session, ttl_seconds=ttl_seconds, now=stamp):
            if not create:
                _mark_session_ended(session, ended_at=stamp, source=source or _MESSAGE_SOURCE)
                return None
            session.session_key = uuid.uuid4()
            session.started_at = stamp

        session.last_seen_source = (source or _MESSAGE_SOURCE)[:32]
        session.last_seen_at = stamp
        session.ended_at = None
        session.save(
            update_fields=[
                "session_key",
                "started_at",
                "last_seen_at",
                "last_seen_source",
                "ended_at",
            ]
        )

    return SessionResult(session=session, ttl_seconds=ttl_seconds)


def get_active_web_session(
    agent: PersistentAgent,
    user,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> Optional[PersistentAgentWebSession]:
    stamp = _now()
    try:
        session = PersistentAgentWebSession.objects.get(agent=agent, user=user)
    except PersistentAgentWebSession.DoesNotExist:
        return None

    if not _is_session_live(session, ttl_seconds=ttl_seconds, now=stamp):
        _mark_session_ended(session, ended_at=stamp)
        return None

    return session


def get_active_web_sessions(
    agent: PersistentAgent,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> Iterable[PersistentAgentWebSession]:
    threshold = _now() - timedelta(seconds=ttl_seconds)
    sessions = (
        PersistentAgentWebSession.objects
        .filter(agent=agent, ended_at__isnull=True, last_seen_at__gte=threshold)
        .select_related("user")
        .order_by("-last_seen_at")
    )
    # Touch expired sessions lazily
    for session in sessions:
        if _is_session_live(session, ttl_seconds=ttl_seconds):
            yield session
        else:
            _mark_session_ended(session)


__all__ = [
    "WEB_SESSION_TTL_SECONDS",
    "SessionResult",
    "start_web_session",
    "heartbeat_web_session",
    "end_web_session",
    "touch_web_session",
    "get_active_web_session",
    "get_active_web_sessions",
]

def delete_expired_sessions(*, batch_size: int = 500) -> int:
    """Remove sessions that have passed the retention window."""
    now = _now()
    cutoff = now - timedelta(days=WEB_SESSION_RETENTION_DAYS)
    stale_cutoff = now - timedelta(minutes=WEB_SESSION_STALE_GRACE_MINUTES)

    total_deleted = 0

    while True:
        expired_ids = list(
            PersistentAgentWebSession.objects.filter(
                ended_at__isnull=False,
                ended_at__lt=cutoff,
            )
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )

        if not expired_ids:
            break

        deleted, _ = PersistentAgentWebSession.objects.filter(id__in=expired_ids).delete()
        total_deleted += deleted
        if deleted < batch_size:
            break

    # Clean stray rows that never closed but are clearly stale
    stale_ids = list(
        PersistentAgentWebSession.objects.filter(
            ended_at__isnull=True,
            last_seen_at__lt=stale_cutoff,
        )
        .order_by("id")
        .values_list("id", flat=True)[:batch_size]
    )
    if stale_ids:
        deleted, _ = PersistentAgentWebSession.objects.filter(id__in=stale_ids).delete()
        total_deleted += deleted

    return total_deleted
