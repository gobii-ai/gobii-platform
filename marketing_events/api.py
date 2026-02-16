from django.conf import settings

from .context import extract_click_context
from .tasks import enqueue_marketing_event


def capi(user, event_name, properties=None, request=None, context=None, provider_targets=None):
    """
    Public entrypoint. Call from views/services to emit a marketing event.
    """
    if not getattr(settings, "GOBII_PROPRIETARY_MODE", False):
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
    if provider_targets:
        payload["provider_targets"] = provider_targets
    enqueue_marketing_event.delay(payload)
