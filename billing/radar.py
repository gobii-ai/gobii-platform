from django.utils import timezone

from util.payments_helper import PaymentsHelper

STRIPE_RADAR_SESSION_ID_SESSION_KEY = "stripe_radar_session_id"
STRIPE_RADAR_SESSION_CAPTURED_AT_SESSION_KEY = "stripe_radar_session_captured_at"


def build_stripe_radar_context(*, capture_url: str) -> dict[str, str] | None:
    publishable_key = PaymentsHelper.get_stripe_publishable_key()
    if not publishable_key:
        return None

    return {
        "publishableKey": publishable_key,
        "captureUrl": capture_url,
    }


def store_stripe_radar_session(request, radar_session_id: str) -> str | None:
    normalized = str(radar_session_id or "").strip()
    if not normalized:
        return None

    capped = normalized[:200]
    request.session[STRIPE_RADAR_SESSION_ID_SESSION_KEY] = capped
    request.session[STRIPE_RADAR_SESSION_CAPTURED_AT_SESSION_KEY] = timezone.now().isoformat()
    request.session.modified = True
    return capped
