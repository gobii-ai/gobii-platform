from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.http import HttpRequest
from waffle import get_waffle_flag_model, get_waffle_switch_model


def is_waffle_flag_active(
    flag_name: str,
    request: HttpRequest | None = None,
    *,
    default: bool = False,
) -> bool:
    """Safely evaluate a waffle flag even when the row or DB isn't ready."""
    try:
        Flag = get_waffle_flag_model()
        flag = Flag.objects.filter(name=flag_name).first()
        if flag is None:
            return default
        return flag.is_active(request)
    except (DatabaseError, ImproperlyConfigured):
        return default


def is_waffle_switch_active(switch_name: str, *, default: bool = False) -> bool:
    """Safely evaluate a waffle switch even when the row or DB isn't ready."""
    try:
        Switch = get_waffle_switch_model()
        switch = Switch.get(switch_name)
        if not switch.pk:
            return default
        return switch.is_active()
    except (DatabaseError, ImproperlyConfigured):
        return default
