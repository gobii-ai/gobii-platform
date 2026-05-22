import logging

import stripe
from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def process_billing_return(request) -> None:
    if request.GET.get("seats_success"):
        _process_seat_return(request, success=True)

    if request.GET.get("seats_cancelled"):
        _process_seat_return(request, success=False)


def _process_seat_return(request, *, success: bool) -> None:
    target_info = request.session.pop("org_seat_portal_target", None)
    org_id_for_reattach = None
    if target_info and target_info.get("org_id"):
        org_id_for_reattach = target_info.get("org_id")

    if org_id_for_reattach:
        try:
            from console import views as console_views

            console_views._assign_stripe_api_key()
            if not console_views._reattach_overage_from_session(request, org_id_for_reattach):
                logger.debug(
                    "No pending overage SKU detach found for org %s on %s redirect.",
                    org_id_for_reattach,
                    "success" if success else "cancel",
                )
        except (stripe.error.StripeError, ImproperlyConfigured) as exc:
            logger.warning(
                "Failed to reattach overage SKU after %s redirect for org %s: %s",
                "success" if success else "cancellation",
                org_id_for_reattach,
                exc,
            )

    if success:
        success_message = "Seat checkout started successfully. Features will unlock once payment completes."
        if target_info and target_info.get("requested"):
            success_message = (
                f"Seat checkout started successfully. In Stripe, update your licensed seat quantity to {target_info.get('requested')}."
            )
        messages.success(request, success_message, fail_silently=True)
    else:
        messages.info(request, "Seat checkout was cancelled before completion.", fail_silently=True)
