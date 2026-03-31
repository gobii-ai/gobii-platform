"""Central helpers for optional third-party integrations."""

from dataclasses import dataclass
from django.conf import settings


@dataclass(frozen=True)
class IntegrationStatus:
    enabled: bool
    reason: str = ""


class IntegrationDisabledError(RuntimeError):
    """Raised when an integration is used while disabled."""


def _status(flag_name: str, reason_name: str = "") -> IntegrationStatus:
    enabled = bool(getattr(settings, flag_name, False))
    reason = getattr(settings, reason_name, "") if reason_name else ""
    return IntegrationStatus(enabled=enabled, reason=reason)


def stripe_status() -> IntegrationStatus:
    return _status("STRIPE_ENABLED", "STRIPE_DISABLED_REASON")


def mailgun_status() -> IntegrationStatus:
    return _status("MAILGUN_ENABLED")


def postmark_status() -> IntegrationStatus:
    return _status("POSTMARK_ENABLED")


def twilio_status() -> IntegrationStatus:
    return _status("TWILIO_ENABLED", "TWILIO_DISABLED_REASON")


def pipedream_status() -> IntegrationStatus:
    enabled = bool(
        settings.PIPEDREAM_CLIENT_ID
        and settings.PIPEDREAM_CLIENT_SECRET
        and settings.PIPEDREAM_PROJECT_ID
    )
    return IntegrationStatus(
        enabled=enabled,
        reason=(
            ""
            if enabled
            else (
                "Pipedream integration is not configured. "
                "Set PIPEDREAM_CLIENT_ID, PIPEDREAM_CLIENT_SECRET, and PIPEDREAM_PROJECT_ID."
            )
        ),
    )


def ensure_stripe_enabled() -> None:
    status = stripe_status()
    if not status.enabled:
        raise IntegrationDisabledError(status.reason or "Stripe integration disabled")


def twilio_verify_available() -> bool:
    return bool(getattr(settings, "TWILIO_ENABLED", False) and getattr(settings, "TWILIO_VERIFY_CONFIGURED", False))


def postmark_simulation_active() -> bool:
    if postmark_status().enabled:
        return False
    return bool(getattr(settings, "SIMULATE_EMAIL_DELIVERY", False))
