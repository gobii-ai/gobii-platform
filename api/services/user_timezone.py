from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


USER_TIMEZONE_FALLBACK = "UTC"
OFFPEAK_START_HOUR = 22
OFFPEAK_END_HOUR = 6


def _get_user_preference_model():
    from api.models import UserPreference

    return UserPreference


def normalize_timezone_value(value: object, *, key: str = "user.timezone") -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"Invalid value for '{key}'. Expected an IANA timezone string.")

    candidate = value.strip()
    if not candidate:
        return ""

    try:
        zone = ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid value for '{key}'. Expected an IANA timezone string.") from exc

    return getattr(zone, "key", candidate)


def resolve_user_timezone(user, *, fallback_to_utc: bool = True) -> str:
    UserPreference = _get_user_preference_model()
    resolved = UserPreference.resolve_known_preferences(user)
    timezone_value = resolved.get(UserPreference.KEY_USER_TIMEZONE)
    if isinstance(timezone_value, str):
        normalized = timezone_value.strip()
        if normalized:
            return normalized
    return USER_TIMEZONE_FALLBACK if fallback_to_utc else ""


def maybe_infer_user_timezone(user, timezone_value: object) -> str:
    if not user or not getattr(user, "pk", None):
        return ""

    existing = resolve_user_timezone(user, fallback_to_utc=False)
    if existing:
        return existing

    UserPreference = _get_user_preference_model()
    normalized = normalize_timezone_value(timezone_value, key=UserPreference.KEY_USER_TIMEZONE)
    if not normalized:
        return ""

    UserPreference.update_known_preferences(
        user,
        {UserPreference.KEY_USER_TIMEZONE: normalized},
    )
    return normalized


def is_offpeak_hour(local_hour: int) -> bool:
    return local_hour >= OFFPEAK_START_HOUR or local_hour < OFFPEAK_END_HOUR


def resolve_user_local_time(user, now_value: datetime) -> tuple[datetime, str]:
    timezone_name = resolve_user_timezone(user)
    try:
        return now_value.astimezone(ZoneInfo(timezone_name)), timezone_name
    except ZoneInfoNotFoundError:
        return now_value.astimezone(ZoneInfo(USER_TIMEZONE_FALLBACK)), USER_TIMEZONE_FALLBACK
