"""
Shared cache key helpers for the account info context processor.

We keep these in a separate module so other parts of the app (signals, tasks)
can invalidate the cache without importing the heavy context processor module.
"""

from django.core.cache import cache


ACCOUNT_INFO_CACHE_VERSION = 1


def account_info_cache_key(user_id: object) -> str:
    return f"pages:account_info:v{ACCOUNT_INFO_CACHE_VERSION}:{user_id}"


def account_info_cache_lock_key(user_id: object) -> str:
    return f"{account_info_cache_key(user_id)}:refresh_lock"


def invalidate_account_info_cache(user_id: object) -> None:
    """
    Invalidate cached account info for a user.

    This ensures templates depending on `account.usage.*` don't show stale values
    after credits or plan state changes.
    """
    if user_id in (None, ""):
        return
    cache.delete(account_info_cache_key(user_id))
    cache.delete(account_info_cache_lock_key(user_id))

