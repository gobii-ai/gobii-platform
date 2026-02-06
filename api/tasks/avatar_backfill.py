import logging
from uuid import UUID

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError

from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.core.image_generation_config import is_image_generation_configured
from api.models import PersistentAgent, SystemSetting

logger = logging.getLogger(__name__)

_BACKFILL_CURSOR_KEY = "AGENT_AVATAR_BACKFILL_CURSOR"


def _resolve_positive(value: int | None, default: int) -> int:
    chosen = default if value is None else value
    try:
        return max(0, int(chosen))
    except (TypeError, ValueError):
        return 0


def _read_cursor() -> UUID | None:
    raw_value = (
        SystemSetting.objects.filter(key=_BACKFILL_CURSOR_KEY)
        .values_list("value_text", flat=True)
        .first()
    )
    if not raw_value:
        return None

    try:
        return UUID(raw_value.strip())
    except ValueError:
        logger.warning("Ignoring invalid avatar backfill cursor value: %r", raw_value)
        return None


def _write_cursor(cursor_value: UUID) -> None:
    SystemSetting.objects.update_or_create(
        key=_BACKFILL_CURSOR_KEY,
        defaults={"value_text": str(cursor_value)},
    )


def _base_candidate_queryset():
    return (
        PersistentAgent.objects.filter(
            avatar_requested_hash="",
            visual_description_requested_hash="",
        )
        .exclude(charter="")
        .only(
            "id",
            "name",
            "charter",
            "avatar_charter_hash",
            "avatar_requested_hash",
            "visual_description",
            "visual_description_requested_hash",
        )
        .order_by("id")
    )


def _fetch_candidates(scan_limit: int, cursor: UUID | None) -> list[PersistentAgent]:
    candidates = _base_candidate_queryset()
    if cursor:
        after_cursor = list(candidates.filter(id__gt=cursor)[:scan_limit])
        if after_cursor:
            return after_cursor
    return list(candidates[:scan_limit])


@shared_task(name="api.tasks.schedule_agent_avatar_backfill")
def schedule_agent_avatar_backfill_task(
    batch_size: int | None = None,
    scan_limit: int | None = None,
) -> int:
    """Gradually enqueue avatar generation for existing agents."""
    if not settings.AGENT_AVATAR_BACKFILL_ENABLED:
        return 0

    resolved_batch_size = _resolve_positive(
        batch_size,
        settings.AGENT_AVATAR_BACKFILL_BATCH_SIZE,
    )
    resolved_scan_limit = _resolve_positive(
        scan_limit,
        settings.AGENT_AVATAR_BACKFILL_SCAN_LIMIT,
    )
    if resolved_batch_size <= 0 or resolved_scan_limit <= 0:
        return 0
    if resolved_scan_limit < resolved_batch_size:
        resolved_scan_limit = resolved_batch_size

    if not is_image_generation_configured():
        return 0

    cursor = _read_cursor()
    candidates = _fetch_candidates(resolved_scan_limit, cursor)
    if not candidates:
        return 0

    scheduled = 0
    scanned = 0

    for agent in candidates:
        scanned += 1
        if not (agent.charter or "").strip():
            continue
        if maybe_schedule_agent_avatar(agent):
            scheduled += 1
        if scheduled >= resolved_batch_size:
            break

    last_scanned = candidates[scanned - 1] if scanned else None
    if last_scanned is not None:
        try:
            _write_cursor(last_scanned.id)
        except DatabaseError:
            logger.exception("Failed persisting avatar backfill cursor")

    logger.info(
        "Avatar backfill sweep complete: scheduled=%s scanned=%s batch_size=%s scan_limit=%s cursor=%s",
        scheduled,
        scanned,
        resolved_batch_size,
        resolved_scan_limit,
        str(last_scanned.id) if last_scanned is not None else "none",
    )
    return scheduled
