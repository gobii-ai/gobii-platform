import fnmatch
import logging

from django.db import OperationalError, ProgrammingError

from pages.models import MiniModeCampaignPattern

logger = logging.getLogger(__name__)

MINI_MODE_COOKIE_NAME = "mini-mode"
MINI_MODE_COOKIE_VALUE = "true"
MINI_MODE_COOKIE_MAX_AGE = 60 * 24 * 60 * 60  # 60 days
MINI_MODE_COOKIE_SAMESITE = "Lax"


def _normalize_campaign_value(value: str | None) -> str:
    return (value or "").strip().lower()


def campaign_matches_mini_mode(utm_campaign: str | None) -> bool:
    normalized_campaign = _normalize_campaign_value(utm_campaign)
    if not normalized_campaign:
        return False

    try:
        patterns = MiniModeCampaignPattern.objects.filter(is_active=True).values_list("pattern", flat=True)
    except (OperationalError, ProgrammingError):
        # Deploys can momentarily serve code before migrations have completed.
        logger.warning("Mini mode pattern table is unavailable; skipping campaign match.")
        return False

    for pattern in patterns:
        normalized_pattern = _normalize_campaign_value(pattern)
        if not normalized_pattern:
            continue
        if fnmatch.fnmatchcase(normalized_campaign, normalized_pattern):
            return True
    return False


def set_request_mini_mode(request) -> None:
    request.COOKIES[MINI_MODE_COOKIE_NAME] = MINI_MODE_COOKIE_VALUE


def clear_request_mini_mode(request) -> None:
    request.COOKIES.pop(MINI_MODE_COOKIE_NAME, None)


def set_mini_mode_cookie(response, request) -> None:
    response.set_cookie(
        MINI_MODE_COOKIE_NAME,
        MINI_MODE_COOKIE_VALUE,
        max_age=MINI_MODE_COOKIE_MAX_AGE,
        samesite=MINI_MODE_COOKIE_SAMESITE,
        secure=request.is_secure(),
        httponly=False,
    )


def clear_mini_mode_cookie(response, request) -> None:
    response.set_cookie(
        MINI_MODE_COOKIE_NAME,
        "",
        max_age=0,
        samesite=MINI_MODE_COOKIE_SAMESITE,
        secure=request.is_secure(),
        httponly=False,
    )

