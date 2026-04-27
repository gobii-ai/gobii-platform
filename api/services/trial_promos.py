from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from api.models import (
    TrialPromo,
    TrialPromoRedemption,
    TrialPromoRedemptionStatusChoices,
    UserTrialEligibility,
    UserTrialEligibilityAutoStatusChoices,
    UserTrialEligibilityManualActionChoices,
)
from api.services.trial_abuse import (
    SIGNAL_SOURCE_CHECKOUT,
    evaluate_user_trial_identity_abuse,
    user_has_prior_individual_history,
)
from billing.checkout_metadata import build_checkout_flow_metadata
from util.trial_eligibility import is_trial_decision_allowed


TRIAL_PROMO_SESSION_KEY = "special_access_trial_promo_id"

TRIAL_PROMO_META_ID = "trial_promo_id"
TRIAL_PROMO_META_CODE = "trial_promo_code"
TRIAL_PROMO_META_NAME = "trial_promo_name"
TRIAL_PROMO_META_PLAN = "trial_promo_plan"
TRIAL_PROMO_META_TRIAL_DAYS = "trial_promo_trial_days"
TRIAL_PROMO_META_PAYMENT_REQUIRED = "trial_promo_payment_required"
TRIAL_PROMO_META_REPEAT_ALLOWED = "trial_promo_repeat_allowed"
TRIAL_PROMO_META_ABUSE_FILTERING = "trial_promo_abuse_filtering"
TRIAL_PROMO_META_CREDIT_AMOUNT = "trial_promo_credit_amount"
TRIAL_PROMO_META_REDEMPTION_ID = "trial_promo_redemption_id"


class TrialPromoError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TrialPromoStartDecision:
    allowed: bool
    reason: str = ""


def find_active_trial_promo_by_code(code: str | None, *, now=None) -> TrialPromo | None:
    normalized = TrialPromo.normalize_code(code)
    if not normalized:
        return None

    promo = TrialPromo.objects.filter(code_digest=TrialPromo.digest_code(normalized)).first()
    if promo is None or not promo.is_available(now=now):
        return None
    return promo


def store_trial_promo_in_session(request, promo: TrialPromo) -> None:
    request.session[TRIAL_PROMO_SESSION_KEY] = str(promo.pk)
    request.session.modified = True


def clear_trial_promo_session(request) -> None:
    request.session.pop(TRIAL_PROMO_SESSION_KEY, None)
    request.session.modified = True


def get_session_trial_promo(request) -> TrialPromo | None:
    promo_id = request.session.get(TRIAL_PROMO_SESSION_KEY)
    if not promo_id:
        return None
    try:
        promo = TrialPromo.objects.filter(pk=promo_id).first()
    except (TypeError, ValueError, ValidationError):
        clear_trial_promo_session(request)
        return None
    if promo is None or not promo.is_available():
        clear_trial_promo_session(request)
        return None
    return promo


def get_trial_promo_manual_action(user) -> str:
    if not user or not getattr(user, "pk", None):
        return UserTrialEligibilityManualActionChoices.INHERIT
    eligibility = (
        UserTrialEligibility.objects.filter(user=user)
        .only("manual_action")
        .first()
    )
    if eligibility is None:
        return UserTrialEligibilityManualActionChoices.INHERIT
    return eligibility.manual_action


def can_user_start_trial_promo(
    *,
    user,
    promo: TrialPromo,
    request=None,
) -> TrialPromoStartDecision:
    manual_action = get_trial_promo_manual_action(user)
    if manual_action == UserTrialEligibilityManualActionChoices.DENY_TRIAL:
        return TrialPromoStartDecision(False, "trial_denied")
    if manual_action == UserTrialEligibilityManualActionChoices.ALLOW_TRIAL:
        return TrialPromoStartDecision(True)

    if not promo.repeat_trials_allowed and user_has_prior_individual_history(user):
        return TrialPromoStartDecision(False, "prior_trial_or_subscription")

    if promo.trial_abuse_filtering_enabled:
        result = evaluate_user_trial_identity_abuse(
            user,
            request=request,
            capture_source=SIGNAL_SOURCE_CHECKOUT,
        )
        if not is_trial_decision_allowed(result.decision, request=request):
            return TrialPromoStartDecision(False, result.reason_codes[0] if result.reason_codes else "trial_abuse_filter")

    return TrialPromoStartDecision(True)


def build_trial_promo_metadata(
    promo: TrialPromo,
    *,
    redemption: TrialPromoRedemption | None = None,
) -> dict[str, str]:
    metadata = {
        TRIAL_PROMO_META_ID: str(promo.pk),
        TRIAL_PROMO_META_CODE: str(promo.code_label or ""),
        TRIAL_PROMO_META_NAME: str(promo.name or ""),
        TRIAL_PROMO_META_PLAN: str(promo.plan or ""),
        TRIAL_PROMO_META_TRIAL_DAYS: str(promo.trial_days),
        TRIAL_PROMO_META_PAYMENT_REQUIRED: "true" if promo.payment_method_required else "false",
        TRIAL_PROMO_META_REPEAT_ALLOWED: "true" if promo.repeat_trials_allowed else "false",
        TRIAL_PROMO_META_ABUSE_FILTERING: "true" if promo.trial_abuse_filtering_enabled else "false",
    }
    if promo.trial_credit_amount is not None:
        metadata[TRIAL_PROMO_META_CREDIT_AMOUNT] = str(promo.trial_credit_amount)
    if redemption is not None:
        metadata[TRIAL_PROMO_META_REDEMPTION_ID] = str(redemption.pk)
    return metadata


def build_trial_promo_checkout_metadata(
    base_metadata: Mapping[str, Any],
    *,
    flow_type: str,
    promo: TrialPromo,
    redemption: TrialPromoRedemption,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    promo_metadata = build_trial_promo_metadata(promo, redemption=redemption)
    if extra_metadata:
        promo_metadata.update(extra_metadata)
    return build_checkout_flow_metadata(
        base_metadata,
        flow_type=flow_type,
        extra_metadata=promo_metadata,
    )


def parse_trial_promo_credit_amount(metadata: Mapping[str, Any] | None) -> Decimal | None:
    if not metadata:
        return None
    raw_value = metadata.get(TRIAL_PROMO_META_CREDIT_AMOUNT)
    if raw_value in (None, ""):
        return None
    try:
        amount = Decimal(str(raw_value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return amount


def reserve_trial_promo_redemption(
    *,
    promo: TrialPromo,
    user,
    event_id: str,
    stripe_customer_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> TrialPromoRedemption:
    if not user or not getattr(user, "pk", None):
        raise TrialPromoError("login_required", "Sign in to start this special trial.")

    now = timezone.now()
    with transaction.atomic():
        locked_promo = TrialPromo.objects.select_for_update().get(pk=promo.pk)
        if not locked_promo.is_available(now=now):
            raise TrialPromoError("inactive", "This special access code is no longer active.")

        existing = (
            TrialPromoRedemption.objects.filter(
                promo=locked_promo,
                user=user,
                status__in=TrialPromoRedemption.COUNTED_STATUSES,
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            raise TrialPromoError("already_redeemed", "This special access code has already been used for this account.")

        if locked_promo.max_redemptions is not None:
            used_count = TrialPromoRedemption.objects.filter(
                promo=locked_promo,
                status__in=TrialPromoRedemption.COUNTED_STATUSES,
            ).count()
            if used_count >= locked_promo.max_redemptions:
                raise TrialPromoError("capacity_reached", "This special access code has reached its use limit.")

        return TrialPromoRedemption.objects.create(
            promo=locked_promo,
            user=user,
            status=TrialPromoRedemptionStatusChoices.CHECKOUT_STARTED,
            event_id=event_id,
            stripe_customer_id=str(stripe_customer_id or ""),
            metadata=dict(metadata or {}),
            checkout_started_at=now,
        )


def mark_trial_promo_redemption_checkout_started(
    redemption: TrialPromoRedemption,
    *,
    checkout_session_id: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    updates = ["updated_at"]
    if checkout_session_id:
        redemption.stripe_checkout_session_id = checkout_session_id
        updates.append("stripe_checkout_session_id")
    if metadata:
        merged_metadata = dict(redemption.metadata or {})
        merged_metadata.update(metadata)
        redemption.metadata = merged_metadata
        updates.append("metadata")
    redemption.save(update_fields=updates)


def mark_trial_promo_redemption_failed(redemption: TrialPromoRedemption | None) -> None:
    if redemption is None:
        return
    TrialPromoRedemption.objects.filter(pk=redemption.pk).update(
        status=TrialPromoRedemptionStatusChoices.CHECKOUT_FAILED,
        checkout_failed_at=timezone.now(),
        updated_at=timezone.now(),
    )


def mark_trial_promo_redemption_from_checkout_session(
    *,
    checkout_session_id: str | None,
    status: str,
    stripe_subscription_id: str | None = None,
) -> bool:
    if not checkout_session_id:
        return False

    now = timezone.now()
    updates = {
        "status": status,
        "updated_at": now,
    }
    if stripe_subscription_id:
        updates["stripe_subscription_id"] = str(stripe_subscription_id)
    if status == TrialPromoRedemptionStatusChoices.CHECKOUT_COMPLETED:
        updates["checkout_completed_at"] = now
    elif status == TrialPromoRedemptionStatusChoices.CHECKOUT_EXPIRED:
        updates["checkout_expired_at"] = now

    return bool(
        TrialPromoRedemption.objects.filter(
            stripe_checkout_session_id=checkout_session_id,
        ).update(**updates)
    )


def mark_trial_promo_redemption_subscription(
    *,
    event_id: str | None,
    stripe_subscription_id: str | None,
) -> bool:
    if not event_id or not stripe_subscription_id:
        return False
    return bool(
        TrialPromoRedemption.objects.filter(event_id=event_id)
        .exclude(status=TrialPromoRedemptionStatusChoices.CHECKOUT_FAILED)
        .update(
            stripe_subscription_id=str(stripe_subscription_id),
            updated_at=timezone.now(),
        )
    )
