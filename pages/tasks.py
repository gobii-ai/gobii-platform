import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from pages.context_processors import (
    ACCOUNT_INFO_CACHE_STALE_SECONDS,
    _account_info_cache_key,
    _account_info_cache_lock_key,
    _build_account_info,
)
from pages.homepage_cache import (
    HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
    _build_homepage_pretrained_payload,
    _homepage_pretrained_cache_key,
    _homepage_pretrained_cache_lock_key,
)

logger = logging.getLogger(__name__)


@shared_task(name="pages.refresh_account_info_cache")
def refresh_account_info_cache(user_id: str) -> None:
    User = get_user_model()
    lock_key = _account_info_cache_lock_key(user_id)
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.info("Account info refresh skipped; user not found: %s", user_id)
        cache.delete(lock_key)
        return

    try:
        acct_info = _build_account_info(user)
        cache.set(
            _account_info_cache_key(user.id),
            {"data": acct_info, "refreshed_at": timezone.now().timestamp()},
            timeout=ACCOUNT_INFO_CACHE_STALE_SECONDS,
        )
    except Exception:
        logger.exception("Failed to refresh account info cache for user %s", user_id)
    finally:
        cache.delete(lock_key)


@shared_task(name="pages.refresh_homepage_pretrained_cache")
def refresh_homepage_pretrained_cache() -> None:
    lock_key = _homepage_pretrained_cache_lock_key()
    try:
        payload = _build_homepage_pretrained_payload()
        cache.set(
            _homepage_pretrained_cache_key(),
            {"data": payload, "refreshed_at": timezone.now().timestamp()},
            timeout=HOMEPAGE_PRETRAINED_CACHE_STALE_SECONDS,
        )
    except Exception:
        logger.exception("Failed to refresh homepage pretrained cache")
    finally:
        cache.delete(lock_key)
