from .tasks import enqueue_marketing_event
from .context import extract_click_context


def capi(user, event_name, properties=None, request=None, context=None):
    """
    Public entrypoint. Call from views/services to emit a marketing event.
    """
    payload = {
        "event_name": event_name,
        "properties": properties or {},
        "user": {
            "id": str(getattr(user, "id", "")) or None,
            "email": getattr(user, "email", None),
            "phone": getattr(user, "phone", None),
        },
        "context": (context or {}) | extract_click_context(request),
    }
    enqueue_marketing_event.delay(payload)
