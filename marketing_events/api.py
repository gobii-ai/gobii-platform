import logging
import math
import time

from django.conf import settings
from django.db import DatabaseError

from util.trial_eligibility import (
    is_complete_registration_capi_decision_allowed,
    is_complete_registration_capi_trial_eligibility_enforcement_enabled,
    is_start_trial_capi_decision_allowed,
    is_start_trial_capi_trial_eligibility_enforcement_enabled,
)

from .context import extract_click_context
from .tasks import (
    enqueue_delayed_subscription_guarded_marketing_event,
    enqueue_marketing_event,
    enqueue_start_trial_marketing_event,
)


logger = logging.getLogger(__name__)


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


def _start_trial_eligibility_snapshot(user, *, request=None) -> dict[str, object] | None:
    if not is_start_trial_capi_trial_eligibility_enforcement_enabled(request):
        return None

    user_id = getattr(user, "id", None)
    if not user_id:
        return None

    try:
        from api.models import UserTrialEligibility

        eligibility = UserTrialEligibility.objects.filter(user_id=user_id).only(
            "auto_status",
            "manual_action",
            "reason_codes",
        ).first()
    except DatabaseError:
        logger.warning(
            "Failed to load stored trial eligibility while enqueueing StartTrial CAPI for user %s",
            user_id,
            exc_info=True,
        )
        return None

    if eligibility is None:
        return None

    decision = eligibility.effective_status
    return {
        "decision": decision,
        "manual_action": eligibility.manual_action,
        "reason_codes": list(eligibility.reason_codes or []),
        "send_allowed": is_start_trial_capi_decision_allowed(decision, request=request),
        "decision_source": "stored_trial_eligibility_snapshot",
    }


def _complete_registration_eligibility_snapshot(user, *, request=None) -> dict[str, object] | None:
    if not is_complete_registration_capi_trial_eligibility_enforcement_enabled(request):
        return None

    user_id = getattr(user, "id", None)
    if not user_id:
        return None

    try:
        from api.models import UserTrialEligibility

        eligibility = UserTrialEligibility.objects.filter(user_id=user_id).only(
            "auto_status",
            "manual_action",
            "reason_codes",
        ).first()
    except DatabaseError:
        logger.warning(
            "Failed to load stored trial eligibility while enqueueing CompleteRegistration CAPI for user %s",
            user_id,
            exc_info=True,
        )
        return None

    if eligibility is None:
        return None

    decision = eligibility.effective_status
    return {
        "decision": decision,
        "manual_action": eligibility.manual_action,
        "reason_codes": list(eligibility.reason_codes or []),
        "send_allowed": is_complete_registration_capi_decision_allowed(
            decision,
            request=request,
        ),
        "decision_source": "stored_trial_eligibility_snapshot",
    }


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
    eligibility_snapshot = _start_trial_eligibility_snapshot(user, request=request)
    if eligibility_snapshot is not None:
        payload["start_trial_eligibility"] = eligibility_snapshot

    delay_minutes = max(settings.CAPI_START_TRIAL_DELAY_MINUTES, 0)
    enqueue_start_trial_marketing_event.apply_async(
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
    if event_name == "CompleteRegistration":
        eligibility_snapshot = _complete_registration_eligibility_snapshot(
            user,
            request=request,
        )
        if eligibility_snapshot is not None:
            payload["complete_registration_eligibility"] = eligibility_snapshot
    enqueue_marketing_event.delay(payload)
