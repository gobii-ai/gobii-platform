from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.http import HttpRequest
from waffle import get_waffle_flag_model


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
