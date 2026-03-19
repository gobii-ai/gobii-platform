"""Service helpers for managing persistent agent web chat sessions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from api.models import PersistentAgent, PersistentAgentWebSession

WEB_SESSION_TTL_SECONDS: int = settings.WEB_SESSION_TTL_SECONDS
WEB_SESSION_RETENTION_DAYS: int = settings.WEB_SESSION_RETENTION_DAYS
WEB_SESSION_STALE_GRACE_MINUTES: int = settings.WEB_SESSION_STALE_GRACE_MINUTES
WEB_SESSION_VISIBILITY_GRACE_SECONDS: int = settings.WEB_SESSION_VISIBILITY_GRACE_SECONDS

_HEARTBEAT_SOURCE = "heartbeat"
_START_SOURCE = "start"
_END_SOURCE = "end"
_MESSAGE_SOURCE = "message"
_SSE_SOURCE = "sse"


def _now():
    return timezone.now()


def _deadline(session: PersistentAgentWebSession, *, ttl_seconds: int) -> timezone.datetime:
    return session.last_seen_at + timedelta(seconds=ttl_seconds)


def _visibility_deadline(
    session: PersistentAgentWebSession,
    *,
    grace_seconds: int,
) -> timezone.datetime | None:
    if session.last_visible_at is None:
        return None
    return session.last_visible_at + timedelta(seconds=grace_seconds)


def _is_session_live(
    session: PersistentAgentWebSession,
    *,
    ttl_seconds: int,
    now: Optional[timezone.datetime] = None,
) -> bool:
    reference = now or _now()
    if session.ended_at is not None:
        return False
    return reference <= _deadline(session, ttl_seconds=ttl_seconds)


def _set_visibility(
    session: PersistentAgentWebSession,
    *,
    is_visible: bool,
    stamp: timezone.datetime,
) -> None:
    session.is_visible = bool(is_visible)
    if session.is_visible:
        session.last_visible_at = stamp


def _deliverable_session_queryset(
    *,
    ttl_seconds: int,
    grace_seconds: int,
    now: timezone.datetime,
) -> models.QuerySet[PersistentAgentWebSession]:
    live_threshold = now - timedelta(seconds=ttl_seconds)
    visible_threshold = now - timedelta(seconds=grace_seconds)
    return PersistentAgentWebSession.objects.filter(
        ended_at__isnull=True,
        last_seen_at__gte=live_threshold,
    ).filter(
        models.Q(is_visible=True) | models.Q(last_visible_at__gte=visible_threshold)
    )


def _mark_session_ended(
    session: PersistentAgentWebSession,
    *,
    ended_at: Optional[timezone.datetime] = None,
    source: Optional[str] = None,
) -> PersistentAgentWebSession:
    timestamp = ended_at or _now()
    if session.ended_at is None:
        session.ended_at = timestamp
        if source:
            session.last_seen_source = source[:32]
        session.save(update_fields=["ended_at", "last_seen_source"])
    return session


def _create_session(
    agent: PersistentAgent,
    user,
    *,
    stamp: timezone.datetime,
    source: str,
    is_visible: bool,
) -> PersistentAgentWebSession:
    return PersistentAgentWebSession.objects.create(
        agent=agent,
        user=user,
        session_key=uuid.uuid4(),
        started_at=stamp,
        last_seen_at=stamp,
        last_seen_source=source[:32],
        is_visible=bool(is_visible),
        last_visible_at=stamp if is_visible else None,
    )


def _live_user_session_queryset(
    agent: PersistentAgent,
    user,
    *,
    ttl_seconds: int,
    now: timezone.datetime,
) -> models.QuerySet[PersistentAgentWebSession]:
    live_threshold = now - timedelta(seconds=ttl_seconds)
    return (
        PersistentAgentWebSession.objects.filter(
            agent=agent,
            user=user,
            ended_at__isnull=True,
            last_seen_at__gte=live_threshold,
        )
        .order_by("-last_seen_at", "-started_at")
    )


def _mark_stale_user_sessions_ended(
    agent: PersistentAgent,
    user,
    *,
    ttl_seconds: int,
    now: timezone.datetime,
    source: Optional[str] = None,
) -> None:
    live_threshold = now - timedelta(seconds=ttl_seconds)
    update_fields: dict[str, timezone.datetime | str] = {"ended_at": now}
    if source:
        update_fields["last_seen_source"] = source[:32]
    (
        PersistentAgentWebSession.objects.filter(
            agent=agent,
            user=user,
            ended_at__isnull=True,
            last_seen_at__lt=live_threshold,
        )
        .update(**update_fields)
    )


def _touch_session(
    session: PersistentAgentWebSession,
    *,
    now: Optional[timezone.datetime] = None,
    source: Optional[str] = None,
    is_visible: Optional[bool] = None,
) -> PersistentAgentWebSession:
    stamp = now or _now()
    session.last_seen_at = stamp
    if source:
        session.last_seen_source = source[:32]
    if is_visible is not None:
        _set_visibility(session, is_visible=is_visible, stamp=stamp)
    session.ended_at = None
    session.save(
        update_fields=[
            "last_seen_at",
            "last_seen_source",
            "is_visible",
            "last_visible_at",
            "ended_at",
        ]
    )
    return session


@dataclass(slots=True)
class SessionResult:
    session: PersistentAgentWebSession
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS

    @property
    def expires_at(self) -> timezone.datetime:
        return _deadline(self.session, ttl_seconds=self.ttl_seconds)


def start_web_session(
    agent: PersistentAgent,
    user,
    *,
    source: Optional[str] = None,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    is_visible: bool = True,
) -> SessionResult:
    stamp = _now()
    with transaction.atomic():
        session = _create_session(
            agent,
            user,
            stamp=stamp,
            source=(source or _START_SOURCE),
            is_visible=is_visible,
        )
    return SessionResult(session=session, ttl_seconds=ttl_seconds)


def heartbeat_web_session(
    session_key,
    agent: PersistentAgent,
    user,
    *,
    source: Optional[str] = None,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    is_visible: bool = True,
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
            _mark_session_ended(
                session,
                ended_at=stamp,
                source=(source or _HEARTBEAT_SOURCE),
            )
            raise ValueError("Web session has expired.")

        _touch_session(
            session,
            now=stamp,
            source=(source or _HEARTBEAT_SOURCE),
            is_visible=is_visible,
        )

    return SessionResult(session=session, ttl_seconds=ttl_seconds)


def end_web_session(
    session_key,
    agent: PersistentAgent,
    user,
    *,
    source: Optional[str] = None,
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

        _mark_session_ended(session, source=(source or _END_SOURCE))

    return SessionResult(session=session)


def touch_web_session(
    agent: PersistentAgent,
    user,
    *,
    source: Optional[str] = None,
    create: bool = False,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    is_visible: Optional[bool] = None,
) -> Optional[SessionResult]:
    stamp = _now()
    with transaction.atomic():
        session = (
            _live_user_session_queryset(
                agent,
                user,
                ttl_seconds=ttl_seconds,
                now=stamp,
            )
            .select_for_update()
            .first()
        )
        if session is not None:
            session = _touch_session(
                session,
                now=stamp,
                source=source,
                is_visible=is_visible,
            )
            return SessionResult(session=session, ttl_seconds=ttl_seconds)

        _mark_stale_user_sessions_ended(
            agent,
            user,
            ttl_seconds=ttl_seconds,
            now=stamp,
            source=(source or _MESSAGE_SOURCE),
        )
        if not create:
            return None

        session = _create_session(
            agent,
            user,
            stamp=stamp,
            source=(source or _MESSAGE_SOURCE),
            is_visible=is_visible is not False,
        )
        return SessionResult(session=session, ttl_seconds=ttl_seconds)


def get_active_web_session(
    agent: PersistentAgent,
    user,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> Optional[PersistentAgentWebSession]:
    stamp = _now()
    session = _live_user_session_queryset(
        agent,
        user,
        ttl_seconds=ttl_seconds,
        now=stamp,
    ).first()
    if session is not None:
        return session

    _mark_stale_user_sessions_ended(agent, user, ttl_seconds=ttl_seconds, now=stamp)
    return None


def get_active_web_sessions(
    agent: PersistentAgent,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> Iterable[PersistentAgentWebSession]:
    threshold = _now() - timedelta(seconds=ttl_seconds)
    sessions = (
        PersistentAgentWebSession.objects.filter(
            agent=agent,
            ended_at__isnull=True,
            last_seen_at__gte=threshold,
        )
        .select_related("user")
        .order_by("-last_seen_at")
    )

    for session in sessions:
        if _is_session_live(session, ttl_seconds=ttl_seconds):
            yield session
        else:
            _mark_session_ended(session)


def is_web_session_deliverable(
    session: PersistentAgentWebSession,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    grace_seconds: int = WEB_SESSION_VISIBILITY_GRACE_SECONDS,
    now: Optional[timezone.datetime] = None,
) -> bool:
    stamp = now or _now()
    if not _is_session_live(session, ttl_seconds=ttl_seconds, now=stamp):
        return False
    if session.is_visible:
        return True
    visibility_deadline = _visibility_deadline(session, grace_seconds=grace_seconds)
    if visibility_deadline is None:
        return False
    return stamp <= visibility_deadline


def get_deliverable_web_session(
    agent: PersistentAgent,
    user,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    grace_seconds: int = WEB_SESSION_VISIBILITY_GRACE_SECONDS,
) -> Optional[PersistentAgentWebSession]:
    stamp = _now()
    return (
        _deliverable_session_queryset(
            ttl_seconds=ttl_seconds,
            grace_seconds=grace_seconds,
            now=stamp,
        )
        .filter(agent=agent, user=user)
        .order_by("-last_visible_at", "-last_seen_at", "-started_at")
        .first()
    )


def get_deliverable_web_sessions(
    agent: PersistentAgent,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    grace_seconds: int = WEB_SESSION_VISIBILITY_GRACE_SECONDS,
) -> Iterable[PersistentAgentWebSession]:
    stamp = _now()
    sessions = (
        _deliverable_session_queryset(
            ttl_seconds=ttl_seconds,
            grace_seconds=grace_seconds,
            now=stamp,
        )
        .filter(agent=agent)
        .select_related("user")
        .order_by("-last_visible_at", "-last_seen_at", "-started_at")
    )

    yield from sessions


def get_live_web_sessions_for_environment(
    execution_environment: str,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    now: Optional[timezone.datetime] = None,
) -> Iterable[PersistentAgentWebSession]:
    stamp = now or _now()
    threshold = stamp - timedelta(seconds=ttl_seconds)
    (
        PersistentAgentWebSession.objects.filter(
            ended_at__isnull=True,
            last_seen_at__lt=threshold,
            agent__execution_environment=execution_environment,
            agent__is_deleted=False,
        )
        .update(ended_at=stamp)
    )
    sessions = (
        PersistentAgentWebSession.objects.filter(
            ended_at__isnull=True,
            last_seen_at__gte=threshold,
            agent__execution_environment=execution_environment,
            agent__is_deleted=False,
        )
        .select_related("agent", "user")
        .order_by("-last_seen_at")
    )

    for session in sessions:
        if _is_session_live(session, ttl_seconds=ttl_seconds, now=stamp):
            yield session
        else:
            _mark_session_ended(session, ended_at=stamp)


def has_active_web_session(
    agent: PersistentAgent,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
) -> bool:
    if not isinstance(agent, PersistentAgent):
        return False
    if not getattr(agent, "id", None):
        return False
    threshold = _now() - timedelta(seconds=ttl_seconds)
    return PersistentAgentWebSession.objects.filter(
        agent=agent,
        ended_at__isnull=True,
        last_seen_at__gte=threshold,
    ).exists()


def has_deliverable_web_session(
    agent: PersistentAgent,
    *,
    ttl_seconds: int = WEB_SESSION_TTL_SECONDS,
    grace_seconds: int = WEB_SESSION_VISIBILITY_GRACE_SECONDS,
) -> bool:
    if not isinstance(agent, PersistentAgent):
        return False
    if not getattr(agent, "id", None):
        return False
    stamp = _now()
    return _deliverable_session_queryset(
        ttl_seconds=ttl_seconds,
        grace_seconds=grace_seconds,
        now=stamp,
    ).filter(agent=agent).exists()


def delete_expired_sessions(
    *,
    batch_size: int = 500,
    now: Optional[timezone.datetime] = None,
) -> int:
    timestamp = now or _now()
    cutoff = timestamp - timedelta(days=WEB_SESSION_RETENTION_DAYS)
    stale_cutoff = timestamp - timedelta(minutes=WEB_SESSION_STALE_GRACE_MINUTES)

    total_deleted = 0

    while True:
        expired_ids = list(
            PersistentAgentWebSession.objects
            .filter(ended_at__isnull=False, ended_at__lt=cutoff)
            .order_by("id")
            .values_list("id", flat=True)[:batch_size]
        )
        if not expired_ids:
            break
        deleted, _ = (
            PersistentAgentWebSession.objects.filter(id__in=expired_ids).delete()
        )
        total_deleted += deleted
        if deleted < batch_size:
            break

    stale_ids = list(
        PersistentAgentWebSession.objects
        .filter(ended_at__isnull=True, last_seen_at__lt=stale_cutoff)
        .order_by("id")
        .values_list("id", flat=True)[:batch_size]
    )
    if stale_ids:
        deleted, _ = (
            PersistentAgentWebSession.objects.filter(id__in=stale_ids).delete()
        )
        total_deleted += deleted

    return total_deleted


__all__ = [
    "SessionResult",
    "WEB_SESSION_TTL_SECONDS",
    "WEB_SESSION_VISIBILITY_GRACE_SECONDS",
    "start_web_session",
    "heartbeat_web_session",
    "end_web_session",
    "touch_web_session",
    "get_active_web_session",
    "get_active_web_sessions",
    "is_web_session_deliverable",
    "get_deliverable_web_session",
    "get_deliverable_web_sessions",
    "get_live_web_sessions_for_environment",
    "has_active_web_session",
    "has_deliverable_web_session",
    "delete_expired_sessions",
]
