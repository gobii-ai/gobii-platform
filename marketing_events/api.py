import math
import time

from django.conf import settings

from .context import extract_click_context
from .tasks import (
    enqueue_complete_registration_marketing_event,
    enqueue_delayed_subscription_guarded_marketing_event,
    enqueue_marketing_event,
    enqueue_start_trial_marketing_event,
)


def _build_payload(user, event_name, properties=None, request=None, context=None, provider_targets=None):
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
    return payload


def capi_start_trial(user, properties=None, request=None, context=None, provider_targets=None):
    """
    Specialized StartTrial entrypoint that delays delivery and preserves original event_time.
    """
    if not settings.GOBII_PROPRIETARY_MODE:
        return

    payload = _build_payload(
        user=user,
        event_name="StartTrial",
        properties=properties,
        request=request,
        context=context,
        provider_targets=provider_targets,
    )

    # Preserve trial start timestamp even when delivery is delayed.
    payload["properties"].setdefault("event_time", int(time.time()))

    delay_minutes = max(settings.CAPI_START_TRIAL_DELAY_MINUTES, 0)
    enqueue_start_trial_marketing_event.apply_async(
        args=[payload],
        countdown=delay_minutes * 60,
    )


def capi_complete_registration(user, properties=None, request=None, context=None, provider_targets=None):
    """
    Delay CompleteRegistration so value reflects the user's current plan intent.
    """
    if not settings.GOBII_PROPRIETARY_MODE:
        return

    payload = _build_payload(
        user=user,
        event_name="CompleteRegistration",
        properties=properties,
        request=request,
        context=context,
        provider_targets=provider_targets,
    )

    payload["properties"].setdefault("event_time", int(time.time()))

    delay_minutes = max(settings.CAPI_COMPLETE_REGISTRATION_DELAY_MINUTES, 0)
    enqueue_complete_registration_marketing_event.apply_async(
        args=[payload],
        countdown=delay_minutes * 60,
    )


def capi_delay_subscription_guarded(
    user,
    event_name,
    *,
    countdown_seconds,
    subscription_guard_id=None,
    properties=None,
    request=None,
    context=None,
    provider_targets=None,
):
    """Delay delivery while preserving the original event time and subscription guard."""
    if not settings.GOBII_PROPRIETARY_MODE:
        return

    payload = _build_payload(
        user=user,
        event_name=event_name,
        properties=properties,
        request=request,
        context=context,
        provider_targets=provider_targets,
    )
    payload["properties"].setdefault("event_time", int(time.time()))
    if subscription_guard_id:
        payload["subscription_guard_id"] = str(subscription_guard_id)

    enqueue_delayed_subscription_guarded_marketing_event.apply_async(
        args=[payload],
        countdown=max(int(math.ceil(countdown_seconds)), 0),
    )


def capi(user, event_name, properties=None, request=None, context=None, provider_targets=None):
    """
    Public entrypoint. Call from views/services to emit a marketing event.
    """
    if not settings.GOBII_PROPRIETARY_MODE:
        return
    if event_name == "CompleteRegistration":
        capi_complete_registration(
            user=user,
            properties=properties,
            request=request,
            context=context,
            provider_targets=provider_targets,
        )
        return
    if event_name == "StartTrial":
        capi_start_trial(
            user=user,
            properties=properties,
            request=request,
            context=context,
            provider_targets=provider_targets,
        )
        return

    payload = _build_payload(
        user=user,
        event_name=event_name,
        properties=properties,
        request=request,
        context=context,
        provider_targets=provider_targets,
    )
    enqueue_marketing_event.delay(payload)
