from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from billing.checkout_metadata import STRIPE_CHECKOUT_FLOW_TYPE_TRIAL


def _coerce_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        candidate = value
    else:
        try:
            candidate = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    if timezone.is_naive(candidate):
        candidate = timezone.make_aware(candidate, timezone=dt_timezone.utc)
    return candidate


def _coerce_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _serialize_checkout_context(context) -> dict[str, Any]:
    value = getattr(context, "value", None)
    return {
        "event_id": getattr(context, "event_id", ""),
        "flow_type": getattr(context, "flow_type", ""),
        "plan": getattr(context, "plan", ""),
        "plan_label": getattr(context, "plan_label", ""),
        "value": float(value) if value is not None else None,
        "currency": getattr(context, "currency", ""),
        "checkout_source_url": getattr(context, "checkout_source_url", ""),
        "stripe_checkout_session_id": getattr(context, "stripe_checkout_session_id", ""),
        "stripe_setup_intent_id": getattr(context, "stripe_setup_intent_id", None),
    }


def record_checkout_context(
    *,
    customer_id: str,
    checkout_session_id: str,
    session_created_at: Any,
    flow_type: str,
    event_id: str,
    plan: str | None,
    plan_label: str | None,
    value: Any,
    currency: str | None,
    checkout_source_url: str | None,
) -> None:
    StripeCheckoutContext = apps.get_model("api", "StripeCheckoutContext")
    StripeCheckoutContext.objects.update_or_create(
        stripe_checkout_session_id=checkout_session_id,
        defaults={
            "stripe_customer_id": customer_id,
            "stripe_session_created_at": _coerce_datetime(session_created_at),
            "flow_type": str(flow_type or "").strip(),
            "event_id": str(event_id or "").strip(),
            "plan": str(plan or "").strip(),
            "plan_label": str(plan_label or "").strip(),
            "value": _coerce_decimal(value),
            "currency": str(currency or "").strip().upper(),
            "checkout_source_url": str(checkout_source_url or "").strip(),
        },
    )


def bind_setup_intent_checkout_context(
    *,
    customer_id: str | None,
    setup_intent_id: str | None,
    setup_intent_created_at: Any,
) -> dict[str, Any] | None:
    if not customer_id or not setup_intent_id:
        return None

    StripeCheckoutContext = apps.get_model("api", "StripeCheckoutContext")
    existing = StripeCheckoutContext.objects.filter(stripe_setup_intent_id=setup_intent_id).first()
    if existing is not None:
        return _serialize_checkout_context(existing)

    candidate_qs = (
        StripeCheckoutContext.objects.filter(
            stripe_customer_id=customer_id,
            flow_type=STRIPE_CHECKOUT_FLOW_TYPE_TRIAL,
        )
        .filter(Q(stripe_setup_intent_id__isnull=True))
        .annotate(candidate_created_at=Coalesce("stripe_session_created_at", "created_at"))
    )

    created_at = _coerce_datetime(setup_intent_created_at)
    if created_at is not None:
        filtered_qs = candidate_qs.filter(candidate_created_at__lte=created_at)
        if filtered_qs.exists():
            candidate_qs = filtered_qs

    with transaction.atomic():
        context = (
            candidate_qs.select_for_update()
            .order_by("-candidate_created_at", "-created_at")
            .first()
        )
        if context is None:
            return None

        context.stripe_setup_intent_id = setup_intent_id
        context.save(update_fields=["stripe_setup_intent_id", "updated_at"])

    return _serialize_checkout_context(context)


def get_checkout_context_for_setup_intent(setup_intent_id: str | None) -> dict[str, Any] | None:
    if not setup_intent_id:
        return None

    StripeCheckoutContext = apps.get_model("api", "StripeCheckoutContext")
    context = StripeCheckoutContext.objects.filter(stripe_setup_intent_id=setup_intent_id).first()
    if context is None:
        return None
    return _serialize_checkout_context(context)


def get_checkout_context_for_session(checkout_session_id: str | None) -> dict[str, Any] | None:
    if not checkout_session_id:
        return None

    StripeCheckoutContext = apps.get_model("api", "StripeCheckoutContext")
    context = StripeCheckoutContext.objects.filter(
        stripe_checkout_session_id=checkout_session_id,
    ).first()
    if context is None:
        return None
    return _serialize_checkout_context(context)
