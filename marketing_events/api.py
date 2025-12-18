from django.conf import settings

from .tasks import enqueue_marketing_event
from .context import extract_click_context


def capi(user, event_name, properties=None, request=None, context=None):
    """
    Public entrypoint. Call from views/services to emit a marketing event.
    """
    if not getattr(settings, "GOBII_PROPRIETARY_MODE", False):
        return
    release_env = str(getattr(settings, "GOBII_RELEASE_ENV", "local") or "").lower()
    if release_env not in ("prod", "production"):
        return
    payload = {
        "event_name": event_name,
        "properties": properties or {},
        "user": {
            "id": str(getattr(user, "id", "")) or None,
            "email": getattr(user, "email", None),
            "phone": getattr(user, "phone", None),
        },
        "context": (extract_click_context(request) or {}) | (context or {}),
    }
    enqueue_marketing_event.delay(payload)
