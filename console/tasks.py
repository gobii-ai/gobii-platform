import logging

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from console.home_metrics import (
    CONSOLE_HOME_CACHE_STALE_SECONDS,
    _console_home_cache_key,
    _console_home_cache_lock_key,
    _build_console_home_metrics_for_owner,
    load_console_home_owner,
)

logger = logging.getLogger(__name__)


@shared_task(name="console.refresh_console_home_cache")
def refresh_console_home_cache(owner_type: str, owner_id: str) -> None:
    lock_key = _console_home_cache_lock_key(owner_type, owner_id)
    owner, is_org = load_console_home_owner(owner_type, owner_id)
    if owner is None:
        logger.info("Console home refresh skipped; %s not found: %s", owner_type, owner_id)
        cache.delete(lock_key)
        return

    try:
        metrics = _build_console_home_metrics_for_owner(owner, is_org=is_org)
        cache.set(
            _console_home_cache_key(owner_type, owner.id),
            {"data": metrics, "refreshed_at": timezone.now().timestamp()},
            timeout=CONSOLE_HOME_CACHE_STALE_SECONDS,
        )
    except Exception:
        logger.exception("Failed to refresh console home cache for %s %s", owner_type, owner_id)
    finally:
        cache.delete(lock_key)
