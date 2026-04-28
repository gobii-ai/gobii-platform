import uuid
import json
import hashlib
from datetime import timedelta, datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from numbers import Number
from typing import Any, Mapping
from urllib.parse import unquote

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.conf import settings
from django.db import transaction, DatabaseError
from django.apps import apps
from django.contrib.auth import get_user_model

from allauth.account.signals import email_confirmed, user_signed_up, user_logged_in, user_logged_out
from django.dispatch import receiver

from djstripe.models import Subscription, Customer, Invoice, PaymentIntent, Charge
from djstripe.event_handlers import djstripe_receiver
from observability import traced, trace

from config.plans import PLAN_CONFIG, get_plan_by_product_id
from config.stripe_config import get_stripe_settings
from constants.stripe import (
    ORG_OVERAGE_STATE_META_KEY,
    ORG_OVERAGE_STATE_DETACHED_PENDING,
)
from constants.plans import PlanNames, PlanSlugs
from constants.grant_types import GrantTypeChoices
from dateutil.relativedelta import relativedelta
from tasks.services import TaskCreditService

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from marketing_events.api import capi
from marketing_events.constants import AD_CAPI_PROVIDER_TARGETS
from marketing_events.context import build_marketing_context_from_user, extract_click_context
from marketing_events.telemetry import record_fbc_synthesized
from marketing_events.value_utils import (
    calculate_start_trial_values,
    resolve_start_trial_conversion_rate,
)
import logging
import stripe

from billing.addons import AddonEntitlementService
from billing.checkout_metadata import (
    STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE,
    STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY,
    clear_checkout_customer_metadata,
)
from billing.checkout_context import (
    bind_setup_intent_checkout_context,
)
from billing.lifecycle_classifier import (
    is_subscription_delinquency_entered,
    is_trial_cancel_scheduled,
    is_trial_conversion_charge,
    is_trial_conversion_failure,
    is_trial_conversion_invoice,
    is_trial_ended_non_renewal,
)
from billing.lifecycle_signals import (
    BillingLifecyclePayload,
    SUBSCRIPTION_DELINQUENCY_ENTERED,
    TRIAL_CANCEL_SCHEDULED,
    TRIAL_CONVERSION_FAILED,
    TRIAL_ENDED_NON_RENEWAL,
    emit_billing_lifecycle_event,
)
from billing.plan_resolver import (
    get_plan_context_for_version,
    get_plan_version_by_price_id,
    get_plan_version_by_product_id,
)
from api.models import (
    OrganizationBilling,
    StripeCheckoutContext,
    TrialPromoRedemptionStatusChoices,
    UserAttribution,
    UserBilling,
)
from api.services.dedicated_proxy_service import (
    DedicatedProxyService,
    DedicatedProxyUnavailableError,
)
from api.services.owner_execution_pause import resume_owner_execution
from api.services.referral_service import ReferralService
from api.services.signup_preview import resume_signup_preview_agents_for_user_if_eligible
from api.services.trial_abuse import (
    SIGNUP_GA_CLIENT_COOKIE_NAME,
    SIGNAL_SOURCE_LOGIN,
    SIGNAL_SOURCE_SIGNUP,
    capture_request_identity_signals_and_attribution,
    evaluate_user_trial_eligibility,
)
from api.services.trial_promos import (
    mark_trial_promo_redemption_from_checkout_session,
    mark_trial_promo_redemption_subscription,
    parse_trial_promo_credit_amount,
)
from util.payments_helper import PaymentsHelper
from util.integrations import stripe_status
from util.subscription_helper import (
    _individual_plan_product_ids,
    _individual_plan_price_ids,
    ensure_single_individual_subscription,
    get_active_subscription,
    resolve_plan_from_subscription_data,
    mark_owner_billing_with_plan,
    mark_user_billing_with_plan,
    downgrade_owner_to_free_plan,
    sync_subscription_after_direct_update,
)
from util.trial_eligibility import (
    is_add_payment_info_capi_decision_allowed,
    is_add_payment_info_capi_trial_eligibility_enforcement_enabled,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

UTM_MAPPING = {
    'source': 'utm_source',
    'medium': 'utm_medium',
    'name': 'utm_campaign',
    'content': 'utm_content',
    'term': 'utm_term'
}

CLICK_ID_PARAMS = ('gclid', 'wbraid', 'gbraid', 'msclkid', 'ttclid', 'rdt_cid', 'li_fat_id')
TRIAL_CONVERSION_PAYMENT_FAILED_EVENT = "TrialConversionPaymentFailed"
TRIAL_CONVERSION_PAYMENT_FAILED_FINAL_EVENT = "TrialConversionPaymentFailedFinal"
SUBSCRIPTION_PAYMENT_FAILED_EVENT = "SubscriptionPaymentFailed"


def _normalize_auth_method(method: str | None) -> str:
    normalized = (method or "").strip().lower()
    if normalized == "socialaccount":
        return "social"
    if normalized in {"password", "code"}:
        return "email"
    return ""


def _get_latest_authentication_entry(request) -> Mapping[str, Any] | None:
    methods = request.session.get("account_authentication_methods", [])
    if not methods:
        return None
    latest = methods[-1]
    if isinstance(latest, Mapping):
        return latest
    return None


def _build_auth_tracking_properties(
    request,
    *,
    sociallogin: Any | None = None,
    default_to_email: bool = False,
) -> dict[str, str]:
    if sociallogin is not None:
        provider = (getattr(getattr(sociallogin, "account", None), "provider", None) or "").strip()
        properties = {"auth_method": "social"}
        if provider:
            properties["auth_provider"] = provider
        return properties

    latest = _get_latest_authentication_entry(request)
    if latest is not None:
        auth_method = _normalize_auth_method(str(latest.get("method") or ""))
        properties: dict[str, str] = {}
        if auth_method:
            properties["auth_method"] = auth_method
        provider = (str(latest.get("provider") or "")).strip()
        if auth_method == "social" and provider:
            properties["auth_provider"] = provider
        if properties:
            return properties

    if default_to_email:
        return {"auth_method": "email"}
    return {}


def _get_customer_with_subscriber(customer_id: str | None) -> Customer | None:
    """Fetch a Stripe customer with subscriber eagerly loaded.

    This is used by webhook handlers when the invoice payload is missing
    subscriber details but we still have a customer ID to resolve the actor.
    """
    if not customer_id:
        return None

    try:
        return Customer.objects.select_related("subscriber").filter(id=customer_id).first()
    except Exception:
        logger.debug("Failed to load customer %s for owner resolution", customer_id, exc_info=True)
        return None


def _get_stripe_data_value(container: Any, key: str) -> Any:
    """Fetch a key from Stripe payloads regardless of dict/object shape."""
    if not container:
        return None
    if isinstance(container, Mapping):
        return container.get(key)
    try:
        return getattr(container, key)
    except AttributeError:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    """Normalise Stripe timestamps to aware datetimes."""
    if value in (None, ""):
        return None

    candidate: datetime | None = None

    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, Number):
        try:
            candidate = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            candidate = None
    elif isinstance(value, str):
        parsed = parse_datetime(value.strip()) if value.strip() else None
        if parsed is not None:
            candidate = parsed
        else:
            try:
                candidate = datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
            except (OverflowError, OSError, ValueError):
                candidate = None

    if candidate is None:
        return None

    if timezone.is_naive(candidate):
        candidate = timezone.make_aware(candidate, timezone=dt_timezone.utc)

    return candidate


def _coerce_bool(value: Any) -> bool | None:
    """Convert Stripe boolean-ish values to strict bools."""
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return None


def _is_final_payment_attempt(invoice_payload: Mapping[str, Any] | None) -> bool | None:
    """Best-effort signal for whether Stripe will try the invoice again."""
    if not isinstance(invoice_payload, Mapping):
        return None

    next_attempt = invoice_payload.get("next_payment_attempt")
    status = (invoice_payload.get("status") or "").lower()
    auto_advance = _coerce_bool(invoice_payload.get("auto_advance"))

    if next_attempt in (None, "", 0):
        return True
    if status in {"uncollectible", "void"}:
        return True
    if auto_advance is False:
        return True
    return False


def _normalize_currency_code(currency: Any) -> str | None:
    if isinstance(currency, str):
        return currency.upper()
    return None


def _trial_paid_period_end(trial_end_dt: datetime | None, current_period_end_dt: datetime | None) -> datetime | None:
    """Return the end of the first paid period following a trial."""
    base = trial_end_dt or current_period_end_dt
    if not base:
        return None
    try:
        return base + relativedelta(months=1)
    except Exception:
        return base + timedelta(days=30)


def _trial_topoff_amount(
    *,
    owner,
    plan_id: str,
    monthly_credits: int,
    as_of: datetime,
) -> Decimal:
    return _owner_plan_topoff_amount(
        owner=owner,
        monthly_credits=monthly_credits,
        as_of=as_of,
        plan_id=plan_id,
    )


def _trial_start_credit_amount(*, plan_id: str | None, monthly_credits: int) -> Decimal:
    """Return the initial trial grant amount for a plan.

    Scale trials intentionally start smaller to limit abuse; conversion top-off
    logic later restores the account to the full monthly allowance.
    """
    normalized_plan = str(plan_id or "").strip().lower()
    if normalized_plan in {PlanNames.SCALE, PlanSlugs.SCALE}:
        return Decimal(monthly_credits) / Decimal(4)
    return Decimal(monthly_credits)


def _owner_plan_topoff_amount(
    *,
    owner,
    monthly_credits: int,
    as_of: datetime,
    plan_id: str | None = None,
) -> Decimal:
    TaskCredit = apps.get_model("api", "TaskCredit")
    UserModel = get_user_model()
    remaining = Decimal(0)
    filters = {
        "grant_type": GrantTypeChoices.PLAN,
        "additional_task": False,
        "voided": False,
        "granted_date__lte": as_of,
        "expiration_date__gte": as_of,
    }
    if plan_id:
        filters["plan"] = plan_id
    if isinstance(owner, UserModel):
        filters["user"] = owner
    else:
        filters["organization"] = owner
    credits = TaskCredit.objects.filter(**filters)
    for credit in credits:
        remaining += (credit.credits or 0) - (credit.credits_used or 0)
    return Decimal(monthly_credits) - remaining


def _amount_major_units(*candidates: Any) -> float | None:
    """Return the first currency amount (in cents) converted to major units."""
    for cand in candidates:
        if cand is None:
            continue
        try:
            return float(Decimal(str(cand)) / Decimal("100"))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return None


_PLAN_VERSION_PRIMARY_KINDS = ("base", "seat")


def _resolve_plan_version_by_price_id(price_id: str | None):
    if not price_id:
        return None
    for kind in _PLAN_VERSION_PRIMARY_KINDS:
        plan_version = get_plan_version_by_price_id(str(price_id), kind=kind)
        if plan_version:
            return plan_version
    return None


def _resolve_plan_version_by_product_id(product_id: str | None):
    if not product_id:
        return None
    for kind in _PLAN_VERSION_PRIMARY_KINDS:
        plan_version = get_plan_version_by_product_id(str(product_id), kind=kind)
        if plan_version:
            return plan_version
    return None


def _plan_version_primary_ids() -> tuple[set[str], set[str]]:
    try:
        PlanVersionPrice = apps.get_model("api", "PlanVersionPrice")
    except Exception:
        return set(), set()
    rows = (
        PlanVersionPrice.objects
        .filter(kind__in=_PLAN_VERSION_PRIMARY_KINDS)
        .values_list("price_id", "product_id")
    )
    price_ids = {str(price_id) for price_id, _ in rows if price_id}
    product_ids = {str(product_id) for _, product_id in rows if product_id}
    return price_ids, product_ids


def _invoice_lines(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    try:
        return (payload.get("lines") or {}).get("data") or []
    except Exception as e:
        logger.exception(
            "Failed to extract invoice lines from payload: %s",
            e
        )
        return []


def _coerce_invoice_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    lines = payload.get("lines") if isinstance(payload, Mapping) else None
    if isinstance(lines, Mapping) and not hasattr(lines, "auto_paging_iter"):
        try:
            return stripe.Invoice.construct_from(dict(payload), stripe.api_key)
        except Exception:
            logger.warning(
                "Failed to coerce invoice payload %s to Stripe object",
                payload.get("id"),
                exc_info=True,
            )
    return payload


def _coerce_subscription_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if isinstance(items, Mapping) and not hasattr(items, "auto_paging_iter"):
        try:
            return stripe.Subscription.construct_from(dict(payload), stripe.api_key)
        except Exception:
            logger.warning(
                "Failed to coerce subscription payload %s to Stripe object",
                payload.get("id"),
                exc_info=True,
            )
    return payload


def _refresh_subscription_when_payload_mismatches_local(sub, payload: Mapping[str, Any]):
    """Refresh from Stripe when the webhook payload and local row disagree."""
    payload_id = str(payload.get("id") or "").strip()
    payload_status = str(payload.get("status") or "").strip().lower()
    local_status = str(getattr(sub, "status", "") or "").strip().lower()

    if not payload_id or not payload_status or payload_status == local_status:
        return sub, None

    try:
        live_subscription = stripe.Subscription.retrieve(
            payload_id,
            expand=["items.data.price"],
        )
        refreshed_sub = Subscription.sync_from_stripe_data(live_subscription)
        logger.info(
            "Refreshed subscription %s from Stripe after local status mismatch (%s -> %s)",
            payload_id,
            local_status,
            payload_status,
        )
        return refreshed_sub, live_subscription
    except Exception:
        logger.warning(
            "Failed to refresh subscription %s after local status mismatch (%s -> %s)",
            payload_id,
            local_status,
            payload_status,
            exc_info=True,
        )
        return sub, None


def _extract_plan_from_lines(lines: list[Mapping[str, Any]]) -> str | None:
    for line in lines:
        price_info = line.get("price") or {}
        if not price_info:
            price_info = (line.get("pricing") or {}).get("price_details") or {}
        price_id = price_info.get("id") or price_info.get("price")
        if price_id:
            plan_version = _resolve_plan_version_by_price_id(str(price_id))
            if plan_version:
                return plan_version.legacy_plan_code or plan_version.plan.slug
        product = price_info.get("product")
        if isinstance(product, Mapping):
            product = product.get("id")
        if product:
            plan_version = _resolve_plan_version_by_product_id(str(product))
            if plan_version:
                return plan_version.legacy_plan_code or plan_version.plan.slug
            plan = get_plan_by_product_id(product)
            if plan and plan.get("id"):
                return plan.get("id")
    return None


def _calculate_subscription_value_from_lines(lines: list[Mapping[str, Any]]) -> tuple[float | None, str | None]:
    """Return estimated total value (in major units) and currency from invoice lines."""
    candidate = _select_plan_line(lines)
    if not candidate:
        return None, None

    price_info = candidate.get("price") or {}
    if not price_info:
        price_info = (candidate.get("pricing") or {}).get("price_details") or {}

    currency = price_info.get("currency")
    amount = price_info.get("unit_amount")
    if amount is None:
        amount = price_info.get("unit_amount_decimal")
    if amount is None:
        amount = candidate.get("amount") or candidate.get("amount_excluding_tax")

    quantity = candidate.get("quantity")
    if quantity in (None, ""):
        quantity = 1

    value = None
    if amount is not None:
        try:
            amount_dec = Decimal(str(amount))
            quantity_dec = Decimal(str(quantity))
            value = float((amount_dec * quantity_dec) / Decimal("100"))
        except (InvalidOperation, TypeError, ValueError):
            value = None

    if isinstance(currency, str):
        currency = currency.upper()

    return value, currency


def _select_plan_line(lines: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not isinstance(lines, list):
        return None

    plan_price_ids, plan_product_ids = _plan_version_primary_ids()
    plan_products = {str(cfg.get("product_id")) for cfg in PLAN_CONFIG.values() if cfg.get("product_id")}
    plan_products |= plan_product_ids

    candidate = None
    for line in lines:
        price_info = line.get("price") or {}
        if not price_info:
            price_info = (line.get("pricing") or {}).get("price_details") or {}
        price_id = price_info.get("id") or price_info.get("price")
        product_id = price_info.get("product")
        if isinstance(product_id, Mapping):
            product_id = product_id.get("id")

        if (price_id and str(price_id) in plan_price_ids) or (product_id and str(product_id) in plan_products):
            return line

        if candidate is None:
            candidate = line

    return candidate


def _extract_subscription_id(payload: Mapping[str, Any], invoice: Invoice | None) -> str | None:
    subscription_id = None
    if invoice and getattr(invoice, "subscription", None):
        try:
            subscription_id = getattr(invoice.subscription, "id", None) or str(invoice.subscription)
        except Exception:
            subscription_id = None

    if not subscription_id:
        subscription_id = payload.get("subscription")

    if not subscription_id:
        try:
            parent = payload.get("parent") or {}
            if isinstance(parent, Mapping):
                sub_details = parent.get("subscription_details") or {}
                if isinstance(sub_details, Mapping):
                    subscription_id = sub_details.get("subscription")
        except Exception:
            subscription_id = None

    return subscription_id


def _line_period_start(lines: list[Mapping[str, Any]]) -> datetime | None:
    candidate = _select_plan_line(lines)
    if not candidate:
        return None
    period = candidate.get("period") or {}
    if not isinstance(period, Mapping):
        return None
    start = _get_stripe_data_value(period, "start")
    return _coerce_datetime(start)
    return None


def _resolve_invoice_owner(invoice: Invoice | None, payload: Mapping[str, Any]):
    customer = getattr(invoice, "customer", None) if invoice else None
    customer_id = getattr(customer, "id", None) if customer else None
    if not customer_id:
        customer_id = payload.get("customer")

    resolved_customer = customer
    if customer_id and (resolved_customer is None or not getattr(resolved_customer, "subscriber", None)):
        resolved_customer = _get_customer_with_subscriber(customer_id) or resolved_customer

    if resolved_customer and getattr(resolved_customer, "id", None):
        customer_id = getattr(resolved_customer, "id")

    owner = None
    owner_type = ""
    organization_billing: OrganizationBilling | None = None

    if resolved_customer and getattr(resolved_customer, "subscriber", None):
        owner = resolved_customer.subscriber
        owner_type = "user"
    elif customer_id:
        organization_billing = (
            OrganizationBilling.objects.select_related("organization")
            .filter(stripe_customer_id=customer_id)
            .first()
        )
        if organization_billing and organization_billing.organization:
            owner = organization_billing.organization
            owner_type = "organization"

    return owner, owner_type, organization_billing, customer_id


def _resolve_setup_intent_owner(
    payload: Mapping[str, Any],
    payment_method_data: Mapping[str, Any] | None = None,
    customer_id: str | None = None,
):
    resolved_customer_id = customer_id
    if resolved_customer_id is None:
        resolved_customer_id = _extract_stripe_object_id(payload.get("customer"))
    if resolved_customer_id is None:
        resolved_customer_id = _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer"))

    resolved_customer = _get_customer_with_subscriber(resolved_customer_id)
    owner = None
    owner_type = ""
    organization_billing: OrganizationBilling | None = None

    if resolved_customer and getattr(resolved_customer, "subscriber", None):
        owner = resolved_customer.subscriber
        owner_type = "user"
    elif resolved_customer_id:
        organization_billing = (
            OrganizationBilling.objects.select_related("organization")
            .filter(stripe_customer_id=resolved_customer_id)
            .first()
        )
        if organization_billing and organization_billing.organization:
            owner = organization_billing.organization
            owner_type = "organization"

    return owner, owner_type, organization_billing, resolved_customer_id


def _resolve_actor_user_id(owner: Any, owner_type: str) -> int | None:
    if owner_type == "user":
        return getattr(owner, "id", None)
    if owner_type == "organization":
        # Lifecycle events for organization-owned subscriptions currently attribute
        # to the org creator as a stable analytics actor fallback; this is not
        # guaranteed to match the user who initiated the billing action.
        return getattr(owner, "created_by_id", None)
    return None


def _resolve_actor_user(owner: Any, owner_type: str):
    if owner_type == "user":
        return owner
    if owner_type == "organization":
        return getattr(owner, "created_by", None)
    return None


def _build_analytics_identify_traits(user: Any) -> dict[str, Any]:
    traits: dict[str, Any] = {}
    first_name = getattr(user, "first_name", None)
    last_name = getattr(user, "last_name", None)
    email = getattr(user, "email", None)
    username = getattr(user, "username", None)
    date_joined = getattr(user, "date_joined", None)

    if first_name:
        traits["first_name"] = first_name
    if last_name:
        traits["last_name"] = last_name
    if email:
        traits["email"] = email
    if username:
        traits["username"] = username
    if date_joined:
        traits["date_joined"] = date_joined

    return traits


def _build_actor_user_properties(user: Any) -> dict[str, Any]:
    if not user:
        return {}

    properties: dict[str, Any] = {}
    user_id = getattr(user, "id", None)
    email = getattr(user, "email", None)
    username = getattr(user, "username", None)
    full_name = ""
    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        full_name = get_full_name() or ""

    if user_id:
        properties["actor_user_id"] = str(user_id)
    if email:
        properties["actor_user_email"] = email
    if username:
        properties["actor_user_username"] = username
    if full_name:
        properties["actor_user_name"] = full_name

    return properties


def _build_invoice_properties(
    payload: Mapping[str, Any],
    invoice: Invoice | None,
    *,
    customer_id: str | None,
    subscription_id: str | None,
    plan_value: str | None,
    lines: list[Mapping[str, Any]],
    allow_failure_detail_lookup: bool = False,
) -> dict[str, Any]:
    attempt_count = payload.get("attempt_count")
    attempted_flag = _coerce_bool(payload.get("attempted"))
    next_attempt_dt = _coerce_datetime(payload.get("next_payment_attempt"))
    final_attempt = _is_final_payment_attempt(payload)

    currency = _normalize_currency_code(payload.get("currency"))
    amount_due_major = _amount_major_units(payload.get("amount_due"), payload.get("total"))
    amount_paid_major = _amount_major_units(payload.get("amount_paid"))

    properties: dict[str, Any] = {
        "stripe.invoice_id": payload.get("id") or getattr(invoice, "id", None),
        "stripe.invoice_number": payload.get("number") or getattr(invoice, "number", None),
        "stripe.customer_id": customer_id,
        "stripe.subscription_id": subscription_id,
        "billing_reason": payload.get("billing_reason"),
        "collection_method": payload.get("collection_method"),
        "livemode": bool(payload.get("livemode")),
        "amount_due": amount_due_major,
        "amount_paid": amount_paid_major,
        "currency": currency,
        "attempt_number": attempt_count,
        "attempted": attempted_flag,
        "next_payment_attempt_at": next_attempt_dt,
        "final_attempt": final_attempt,
        "status": payload.get("status"),
        "customer_email": payload.get("customer_email"),
        "customer_name": payload.get("customer_name"),
        "hosted_invoice_url": payload.get("hosted_invoice_url"),
        "invoice_pdf": payload.get("invoice_pdf"),
        "line_items": len(lines) if isinstance(lines, list) else None,
        "plan": plan_value,
        "receipt_number": payload.get("receipt_number"),
    }

    status_transitions = payload.get("status_transitions") or {}
    paid_at = _coerce_datetime(status_transitions.get("paid_at"))
    finalized_at = _coerce_datetime(status_transitions.get("finalized_at"))
    if paid_at:
        properties["paid_at"] = paid_at
    if finalized_at:
        properties["finalized_at"] = finalized_at

    metadata = _coerce_metadata_dict(payload.get("metadata"))
    if metadata.get("gobii_event_id"):
        properties["gobii_event_id"] = metadata.get("gobii_event_id")

    price_ids = []
    for line in lines:
        price_info = line.get("price") or {}
        if not price_info:
            price_info = (line.get("pricing") or {}).get("price_details") or {}
        price_id = price_info.get("id") or price_info.get("price")
        if price_id:
            price_ids.append(price_id)
    if price_ids:
        properties["line_price_ids"] = price_ids

    properties.update(
        _extract_invoice_failure_properties(
            payload,
            allow_stripe_lookup=allow_failure_detail_lookup,
        )
    )

    return {k: v for k, v in properties.items() if v not in (None, "")}


def _build_invoice_properties_fallback(
    payload: Mapping[str, Any],
    invoice: Invoice | None,
    *,
    customer_id: str | None,
    subscription_id: str | None,
    plan_value: str | None,
) -> dict[str, Any]:
    """Return a minimal analytics payload when full invoice enrichment fails."""
    properties = {
        "stripe.invoice_id": payload.get("id") or getattr(invoice, "id", None),
        "stripe.invoice_number": payload.get("number") or getattr(invoice, "number", None),
        "stripe.customer_id": customer_id,
        "stripe.subscription_id": subscription_id,
        "billing_reason": payload.get("billing_reason"),
        "collection_method": payload.get("collection_method"),
        "livemode": bool(payload.get("livemode")),
        "amount_due": _amount_major_units(payload.get("amount_due"), payload.get("total")),
        "amount_paid": _amount_major_units(payload.get("amount_paid")),
        "currency": _normalize_currency_code(payload.get("currency")),
        "attempt_number": payload.get("attempt_count"),
        "final_attempt": _is_final_payment_attempt(payload),
        "status": payload.get("status"),
        "customer_email": payload.get("customer_email"),
        "customer_name": payload.get("customer_name"),
        "plan": plan_value,
    }
    return {key: value for key, value in properties.items() if value not in (None, "")}


def _safe_build_invoice_properties(
    payload: Mapping[str, Any],
    invoice: Invoice | None,
    *,
    customer_id: str | None,
    subscription_id: str | None,
    plan_value: str | None,
    lines: list[Mapping[str, Any]],
    allow_failure_detail_lookup: bool = False,
) -> dict[str, Any]:
    """Build invoice analytics properties without allowing enrichment failures to break webhooks."""
    try:
        return _build_invoice_properties(
            payload,
            invoice,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan_value=plan_value,
            lines=lines,
            allow_failure_detail_lookup=allow_failure_detail_lookup,
        )
    except Exception:
        # Best-effort analytics must never break billing webhook processing.
        logger.exception(
            "Failed to build full invoice analytics properties for invoice %s; using fallback payload",
            payload.get("id"),
        )
        return _build_invoice_properties_fallback(
            payload,
            invoice,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan_value=plan_value,
        )


def _safe_client_ip(request) -> str | None:
    """Return a normalized client IP or None if unavailable."""
    if not request:
        return None
    try:
        ip = Analytics.get_client_ip(request)
    except Exception:
        return None
    if not ip or ip == '0':
        return None
    return ip


def _build_marketing_context_from_user(user: Any) -> dict[str, Any]:
    return build_marketing_context_from_user(
        user,
        synthesized_fbc_source="pages.signals.build_marketing_context_from_user",
        record_fbc_synthesized_fn=record_fbc_synthesized,
    )


def _build_off_session_marketing_context_from_user(user: Any) -> dict[str, Any]:
    """Return only durable identifiers that are safe for off-session conversions."""
    context = _build_marketing_context_from_user(user)
    off_session_context: dict[str, Any] = {"consent": context.get("consent", True)}

    ga_client_id = context.get("ga_client_id")
    if ga_client_id:
        off_session_context["ga_client_id"] = ga_client_id

    return off_session_context


def _calculate_subscription_value(licensed_item: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    """Return estimated total value (in major units) and currency for the licensed item."""
    if not isinstance(licensed_item, Mapping):
        return None, None

    price = licensed_item.get("price") or {}
    if not isinstance(price, Mapping):
        price = {}

    currency = price.get("currency")
    amount = price.get("unit_amount")
    if amount is None:
        amount = price.get("unit_amount_decimal")

    quantity = licensed_item.get("quantity")
    if quantity in (None, ""):
        quantity = 1

    value: float | None = None
    if amount is not None:
        try:
            amount_dec = Decimal(str(amount))
            quantity_dec = Decimal(str(quantity))
            value = float((amount_dec * quantity_dec) / Decimal("100"))
        except (InvalidOperation, TypeError, ValueError):
            value = None

    if isinstance(currency, str):
        currency = currency.upper()

    return value, currency


def _extract_plan_value_from_subscription(source: Mapping[str, Any] | None) -> str | None:
    """Derive plan identifier from Stripe subscription payload when available."""
    if not isinstance(source, Mapping):
        return None

    try:
        items = (source.get("items") or {}).get("data", []) or []
    except AttributeError:
        items = []

    for item in items:
        if not isinstance(item, Mapping):
            continue
        plan_info = item.get("plan") or {}
        if not isinstance(plan_info, Mapping):
            continue
        if plan_info.get("usage_type") != "licensed":
            continue
        price_info = item.get("price") or {}
        if not isinstance(price_info, Mapping):
            price_info = {}
        price_id = price_info.get("id") or price_info.get("price")
        if price_id:
            plan_version = _resolve_plan_version_by_price_id(str(price_id))
            if plan_version:
                return plan_version.legacy_plan_code or plan_version.plan.slug
        product_id = price_info.get("product")
        if isinstance(product_id, Mapping):
            product_id = product_id.get("id")
        if not product_id:
            continue
        plan_version = _resolve_plan_version_by_product_id(str(product_id))
        if plan_version:
            return plan_version.legacy_plan_code or plan_version.plan.slug
        plan_config = get_plan_by_product_id(product_id)
        if not plan_config:
            continue
        plan_id = plan_config.get("id")
        if not plan_id:
            continue
        return plan_id

    return None


def _coerce_metadata_dict(candidate: Any) -> dict[str, Any]:
    """Best effort conversion of Stripe object-like mappings to plain dicts."""
    if not candidate:
        return {}
    if isinstance(candidate, Mapping):
        return dict(candidate)
    try:
        return dict(candidate)
    except Exception:
        try:
            keys = list(candidate.keys())  # type: ignore[attr-defined]
        except Exception:
            return {}
        result = {}
        for key in keys:
            try:
                result[key] = candidate[key]  # type: ignore[index]
            except Exception:
                try:
                    result[key] = getattr(candidate, key)
                except Exception:
                    continue
        return result


def _clear_customer_checkout_context_if_matches(*, customer_id: str | None, expected_event_id: str | None) -> bool:
    """Clear transient customer checkout metadata when the event matches the active checkout."""
    if not customer_id or not expected_event_id:
        return False

    customer_payload = stripe.Customer.retrieve(customer_id, api_key=stripe.api_key)
    customer_metadata = _coerce_metadata_dict(_get_stripe_data_value(customer_payload, "metadata"))
    current_event_id = str(customer_metadata.get(STRIPE_CHECKOUT_CUSTOMER_EVENT_ID_META_KEY) or "").strip()
    if current_event_id != expected_event_id:
        return False

    stripe.Customer.modify(
        customer_id,
        metadata=clear_checkout_customer_metadata(),
        api_key=stripe.api_key,
    )
    return True


def _emit_add_payment_info_from_checkout_context(
    *,
    owner,
    checkout_context: Mapping[str, Any],
    payment_method_type: str | None,
) -> bool:
    eligibility_snapshot = _add_payment_info_eligibility_snapshot(owner)
    if eligibility_snapshot is not None and eligibility_snapshot.get("send_allowed") is False:
        _track_add_payment_info_trial_eligibility_skip(
            owner=owner,
            snapshot=eligibility_snapshot,
            value=checkout_context.get("value"),
        )
        return False

    marketing_properties = {
        "event_id": checkout_context.get("event_id"),
        "plan": checkout_context.get("plan"),
        "plan_label": checkout_context.get("plan_label"),
        "flow_type": checkout_context.get("flow_type"),
        "value": checkout_context.get("value"),
        "currency": checkout_context.get("currency"),
        "payment_method_type": payment_method_type,
    }
    marketing_properties = {
        key: value
        for key, value in marketing_properties.items()
        if value not in (None, "")
    }

    marketing_context = _build_marketing_context_from_user(owner)
    checkout_source_url = checkout_context.get("checkout_source_url")
    if checkout_source_url:
        marketing_context["page"] = {"url": checkout_source_url}

    capi(
        user=owner,
        event_name="AddPaymentInfo",
        properties=marketing_properties,
        request=None,
        context=marketing_context,
    )
    return True


def _serialize_checkout_context_record(checkout_context: StripeCheckoutContext) -> dict[str, Any]:
    value = getattr(checkout_context, "value", None)
    return {
        "event_id": getattr(checkout_context, "event_id", ""),
        "flow_type": getattr(checkout_context, "flow_type", ""),
        "plan": getattr(checkout_context, "plan", ""),
        "plan_label": getattr(checkout_context, "plan_label", ""),
        "value": float(value) if value is not None else None,
        "currency": getattr(checkout_context, "currency", ""),
        "checkout_source_url": getattr(checkout_context, "checkout_source_url", ""),
        "stripe_checkout_session_id": getattr(checkout_context, "stripe_checkout_session_id", ""),
        "stripe_setup_intent_id": getattr(checkout_context, "stripe_setup_intent_id", None),
    }


def _emit_add_payment_info_for_checkout_context_once(
    *,
    owner,
    checkout_context: StripeCheckoutContext,
    payment_method_type: str | None,
) -> bool:
    if checkout_context.add_payment_info_sent_at is not None:
        return False

    did_enqueue = _emit_add_payment_info_from_checkout_context(
        owner=owner,
        checkout_context=_serialize_checkout_context_record(checkout_context),
        payment_method_type=payment_method_type,
    )
    if not did_enqueue:
        return False

    checkout_context.add_payment_info_sent_at = timezone.now()
    checkout_context.save(update_fields=["add_payment_info_sent_at", "updated_at"])
    return True


def _emit_add_payment_info_for_setup_intent(
    *,
    owner,
    owner_type: str,
    payload: Mapping[str, Any],
) -> None:
    if owner_type != "user" or not owner:
        return

    setup_intent_id = _extract_stripe_object_id(payload)
    if not setup_intent_id:
        return

    payment_method_types = payload.get("payment_method_types")
    payment_method_type = None
    if isinstance(payment_method_types, list) and payment_method_types:
        payment_method_type = str(payment_method_types[0]).strip() or None

    with transaction.atomic():
        checkout_context = (
            StripeCheckoutContext.objects.select_for_update()
            .filter(stripe_setup_intent_id=setup_intent_id)
            .first()
        )
        if checkout_context is None:
            return

        _emit_add_payment_info_for_checkout_context_once(
            owner=owner,
            checkout_context=checkout_context,
            payment_method_type=payment_method_type,
        )


def _emit_add_payment_info_for_completed_checkout_session(
    *,
    payload: Mapping[str, Any],
    customer_id: str | None,
) -> None:
    metadata = _coerce_metadata_dict(payload.get("metadata"))
    if metadata.get("flow_type") != STRIPE_CHECKOUT_FLOW_TYPE_PURCHASE:
        return

    owner, owner_type, _organization_billing, resolved_customer_id = _resolve_setup_intent_owner(
        payload,
        customer_id=customer_id,
    )
    if not owner:
        logger.info(
            "Unable to resolve owner for checkout session %s (customer=%s); skipping purchase AddPaymentInfo",
            payload.get("id"),
            customer_id,
        )
        return

    if owner_type != "user" or not resolved_customer_id:
        return

    payment_method_types = payload.get("payment_method_types")
    payment_method_type = None
    if isinstance(payment_method_types, list) and payment_method_types:
        payment_method_type = str(payment_method_types[0]).strip() or None

    with transaction.atomic():
        checkout_context = (
            StripeCheckoutContext.objects.select_for_update()
            .filter(stripe_checkout_session_id=_extract_stripe_object_id(payload))
            .first()
        )
        if checkout_context is None:
            return

        _emit_add_payment_info_for_checkout_context_once(
            owner=owner,
            checkout_context=checkout_context,
            payment_method_type=payment_method_type,
        )


def _add_payment_info_eligibility_snapshot(user: Any) -> dict[str, Any] | None:
    if not is_add_payment_info_capi_trial_eligibility_enforcement_enabled():
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
            "Failed to load stored trial eligibility while preparing AddPaymentInfo CAPI for user %s",
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
        "send_allowed": is_add_payment_info_capi_decision_allowed(decision),
        "decision_source": "stored_trial_eligibility_snapshot",
    }


def _track_add_payment_info_trial_eligibility_skip(
    *,
    owner: Any,
    snapshot: Mapping[str, Any],
    value: Any = None,
) -> None:
    owner_id = getattr(owner, "id", None)
    properties = {
        "event_name": "AddPaymentInfo",
        "reason": "trial_eligibility_disallowed",
        "decision_source": snapshot.get("decision_source"),
        "trial_eligibility_decision": snapshot.get("decision"),
        "trial_eligibility_manual_action": snapshot.get("manual_action"),
        "trial_eligibility_reason_codes": list(snapshot.get("reason_codes") or []),
        "trial_eligibility_policy_send_allowed": False,
    }
    if value is not None:
        properties["value"] = value
    Analytics.track(
        user_id=owner_id,
        event=AnalyticsEvent.CAPI_EVENT_SKIPPED,
        properties=properties,
    )


def _extract_stripe_object_id(candidate: Any) -> str | None:
    if candidate in (None, ""):
        return None
    if isinstance(candidate, str):
        stripped = candidate.strip()
        return stripped or None
    object_id = _get_stripe_data_value(candidate, "id")
    if isinstance(object_id, str):
        stripped = object_id.strip()
        return stripped or None
    return None


def _first_present(*candidates: Any) -> Any | None:
    for candidate in candidates:
        if candidate not in (None, ""):
            return candidate
    return None


def _retrieve_payment_intent_data(payment_intent_id: str | None) -> dict[str, Any]:
    if not payment_intent_id:
        return {}
    try:
        payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    except stripe.error.StripeError:
        logger.info(
            "Unable to retrieve Stripe payment intent %s for failure analytics",
            payment_intent_id,
            exc_info=True,
        )
        return {}
    return _coerce_metadata_dict(payment_intent)


def _get_djstripe_payment_intent_data(payment_intent_id: str | None) -> dict[str, Any]:
    if not payment_intent_id:
        return {}
    try:
        payment_intent = PaymentIntent.objects.filter(id=payment_intent_id).first()
    except DatabaseError:
        logger.info(
            "Unable to load dj-stripe payment intent %s for failure analytics",
            payment_intent_id,
            exc_info=True,
        )
        return {}
    if not payment_intent:
        return {}
    return _coerce_metadata_dict(getattr(payment_intent, "stripe_data", None))


def _retrieve_charge_data(charge_id: str | None) -> dict[str, Any]:
    if not charge_id:
        return {}
    try:
        charge = stripe.Charge.retrieve(charge_id)
    except stripe.error.StripeError:
        logger.info(
            "Unable to retrieve Stripe charge %s for failure analytics",
            charge_id,
            exc_info=True,
        )
        return {}
    return _coerce_metadata_dict(charge)


def _get_djstripe_charge_data(charge_id: str | None) -> dict[str, Any]:
    if not charge_id:
        return {}
    try:
        charge = Charge.objects.filter(id=charge_id).first()
    except DatabaseError:
        logger.info(
            "Unable to load dj-stripe charge %s for failure analytics",
            charge_id,
            exc_info=True,
        )
        return {}
    if not charge:
        return {}
    return _coerce_metadata_dict(getattr(charge, "stripe_data", None))


def _merge_charge_failure_details(
    charge_data: Mapping[str, Any],
    *,
    failure_message: Any | None,
    failure_code: Any | None,
    decline_code: Any | None,
    failure_type: Any | None,
) -> tuple[Any | None, Any | None, Any | None, Any | None]:
    charge_outcome_data = _coerce_metadata_dict(_get_stripe_data_value(charge_data, "outcome"))
    failure_message = _first_present(
        failure_message,
        _get_stripe_data_value(charge_data, "failure_message"),
        _get_stripe_data_value(charge_outcome_data, "seller_message"),
    )
    failure_code = _first_present(
        failure_code,
        _get_stripe_data_value(charge_data, "failure_code"),
    )
    decline_code = _first_present(
        decline_code,
        _get_stripe_data_value(charge_outcome_data, "reason"),
    )
    failure_reason = _first_present(
        failure_message,
        decline_code,
        failure_code,
        failure_type,
    )
    return failure_message, failure_code, decline_code, failure_reason


def _extract_invoice_failure_properties(
    payload: Mapping[str, Any],
    *,
    allow_stripe_lookup: bool = False,
) -> dict[str, Any]:
    payment_intent_data = _coerce_metadata_dict(payload.get("payment_intent"))
    payment_intent_id = _extract_stripe_object_id(payload.get("payment_intent"))
    payment_error_data: dict[str, Any] = {}
    charge_data: dict[str, Any] = {}
    charge_id = _extract_stripe_object_id(payload.get("charge"))

    payments = _coerce_metadata_dict(payload.get("payments"))
    payment_entries = payments.get("data") or []
    if isinstance(payment_entries, list):
        for payment_entry in payment_entries:
            payment_entry_data = _coerce_metadata_dict(payment_entry)
            payment_data = _coerce_metadata_dict(payment_entry_data.get("payment"))
            if not payment_data:
                continue

            if payment_intent_id is None:
                payment_intent_id = _extract_stripe_object_id(payment_data.get("payment_intent"))
            if not payment_intent_data:
                payment_intent_data = _coerce_metadata_dict(payment_data.get("payment_intent"))
            if charge_id is None:
                charge_id = _extract_stripe_object_id(payment_data.get("charge"))
            if not charge_data:
                charge_data = _coerce_metadata_dict(payment_data.get("charge"))

    if allow_stripe_lookup and payment_intent_id and not payment_intent_data:
        payment_intent_data = _get_djstripe_payment_intent_data(payment_intent_id)
    if allow_stripe_lookup and payment_intent_id and not payment_intent_data:
        payment_intent_data = _retrieve_payment_intent_data(payment_intent_id)

    if not payment_error_data and payment_intent_data:
        payment_error_data = _coerce_metadata_dict(_get_stripe_data_value(payment_intent_data, "last_payment_error"))
    if charge_id is None:
        charge_id = _extract_stripe_object_id(_get_stripe_data_value(payment_error_data, "charge"))
    if not charge_data and payment_intent_data:
        charge_data = _coerce_metadata_dict(_get_stripe_data_value(payment_intent_data, "latest_charge"))
    if charge_id is None:
        charge_id = _extract_stripe_object_id(_get_stripe_data_value(payment_intent_data, "latest_charge"))
    if charge_id is None:
        charge_id = _extract_stripe_object_id(charge_data)

    payment_method_data = _coerce_metadata_dict(_get_stripe_data_value(payment_error_data, "payment_method"))
    payment_method_types = _get_stripe_data_value(payment_intent_data, "payment_method_types")
    payment_method_type = _get_stripe_data_value(payment_method_data, "type")
    if payment_method_type in (None, "") and isinstance(payment_method_types, list) and payment_method_types:
        payment_method_type = payment_method_types[0]

    failure_type = _get_stripe_data_value(payment_error_data, "type")
    failure_message, failure_code, decline_code, failure_reason = _merge_charge_failure_details(
        charge_data,
        failure_message=_get_stripe_data_value(payment_error_data, "message"),
        failure_code=_get_stripe_data_value(payment_error_data, "code"),
        decline_code=_get_stripe_data_value(payment_error_data, "decline_code"),
        failure_type=failure_type,
    )

    if allow_stripe_lookup and charge_id and not charge_data and not failure_reason:
        charge_data = _get_djstripe_charge_data(charge_id)
        failure_message, failure_code, decline_code, failure_reason = _merge_charge_failure_details(
            charge_data,
            failure_message=failure_message,
            failure_code=failure_code,
            decline_code=decline_code,
            failure_type=failure_type,
        )
    if allow_stripe_lookup and charge_id and not charge_data and not failure_reason:
        charge_data = _retrieve_charge_data(charge_id)
        failure_message, failure_code, decline_code, failure_reason = _merge_charge_failure_details(
            charge_data,
            failure_message=failure_message,
            failure_code=failure_code,
            decline_code=decline_code,
            failure_type=failure_type,
        )

    properties: dict[str, Any] = {}
    if payment_intent_id:
        properties["stripe.payment_intent_id"] = payment_intent_id
    if charge_id:
        properties["stripe.charge_id"] = charge_id
    if failure_reason:
        properties["failure_reason"] = failure_reason
    if failure_message:
        properties["failure_message"] = failure_message
    if failure_code:
        properties["failure_code"] = failure_code
    if decline_code:
        properties["decline_code"] = decline_code
    if failure_type:
        properties["failure_type"] = failure_type
    if payment_method_type:
        properties["payment_method_type"] = payment_method_type

    return properties


def _retrieve_setup_intent_data(setup_intent_id: str | None) -> dict[str, Any]:
    if not setup_intent_id:
        return {}
    try:
        setup_intent = stripe.SetupIntent.retrieve(
            setup_intent_id,
            expand=["payment_method", "last_setup_error.payment_method"],
        )
    except stripe.error.StripeError:
        logger.info(
            "Unable to retrieve Stripe setup intent %s for setup intent analytics",
            setup_intent_id,
            exc_info=True,
        )
        return {}
    return _coerce_metadata_dict(setup_intent)


def _retrieve_payment_method_data(payment_method_id: str | None) -> dict[str, Any]:
    if not payment_method_id:
        return {}
    try:
        payment_method = stripe.PaymentMethod.retrieve(payment_method_id)
    except stripe.error.StripeError:
        logger.info(
            "Unable to retrieve Stripe payment method %s for setup intent analytics",
            payment_method_id,
            exc_info=True,
        )
        return {}
    return _coerce_metadata_dict(payment_method)


def _extract_payment_method_analytics_properties(
    payment_method_data: Mapping[str, Any] | None,
    *,
    fallback_type: Any | None = None,
) -> dict[str, Any]:
    normalized_payment_method = _coerce_metadata_dict(payment_method_data)
    if not normalized_payment_method:
        return {}

    payment_method_id = _extract_stripe_object_id(normalized_payment_method)
    payment_method_type = _first_present(
        _get_stripe_data_value(normalized_payment_method, "type"),
        fallback_type,
    )

    type_data = {}
    if isinstance(payment_method_type, str) and payment_method_type:
        type_data = _coerce_metadata_dict(
            _get_stripe_data_value(normalized_payment_method, payment_method_type)
        )

    generated_from_data = _coerce_metadata_dict(_get_stripe_data_value(type_data, "generated_from"))
    generated_payment_method_details = _coerce_metadata_dict(
        _get_stripe_data_value(generated_from_data, "payment_method_details")
    )
    generated_type = _get_stripe_data_value(generated_payment_method_details, "type")
    generated_type_data = {}
    if isinstance(generated_type, str) and generated_type:
        generated_type_data = _coerce_metadata_dict(
            _get_stripe_data_value(generated_payment_method_details, generated_type)
        )

    card_present_data = _coerce_metadata_dict(_get_stripe_data_value(normalized_payment_method, "card_present"))
    interac_present_data = _coerce_metadata_dict(
        _get_stripe_data_value(normalized_payment_method, "interac_present")
    )
    type_networks = _coerce_metadata_dict(_get_stripe_data_value(type_data, "networks"))
    generated_type_networks = _coerce_metadata_dict(
        _get_stripe_data_value(generated_type_data, "networks")
    )
    card_present_networks = _coerce_metadata_dict(_get_stripe_data_value(card_present_data, "networks"))
    interac_present_networks = _coerce_metadata_dict(
        _get_stripe_data_value(interac_present_data, "networks")
    )
    billing_details = _coerce_metadata_dict(_get_stripe_data_value(normalized_payment_method, "billing_details"))
    billing_address = _coerce_metadata_dict(_get_stripe_data_value(billing_details, "address"))
    link_data = _coerce_metadata_dict(_get_stripe_data_value(normalized_payment_method, "link"))

    payment_method_brand = _first_present(
        _get_stripe_data_value(type_data, "display_brand"),
        _get_stripe_data_value(type_data, "brand"),
        _get_stripe_data_value(card_present_data, "brand"),
        _get_stripe_data_value(interac_present_data, "brand"),
        _get_stripe_data_value(generated_type_data, "brand"),
    )
    payment_method_display_brand = _first_present(
        _get_stripe_data_value(type_data, "display_brand"),
        _get_stripe_data_value(generated_type_data, "display_brand"),
    )
    payment_method_last4 = _first_present(
        _get_stripe_data_value(type_data, "last4"),
        _get_stripe_data_value(card_present_data, "last4"),
        _get_stripe_data_value(interac_present_data, "last4"),
        _get_stripe_data_value(generated_type_data, "last4"),
    )
    payment_method_fingerprint = _first_present(
        _get_stripe_data_value(type_data, "fingerprint"),
        _get_stripe_data_value(card_present_data, "fingerprint"),
        _get_stripe_data_value(interac_present_data, "fingerprint"),
        _get_stripe_data_value(generated_type_data, "fingerprint"),
    )
    payment_method_funding = _first_present(
        _get_stripe_data_value(type_data, "funding"),
        _get_stripe_data_value(card_present_data, "funding"),
        _get_stripe_data_value(interac_present_data, "funding"),
        _get_stripe_data_value(generated_type_data, "funding"),
    )
    payment_method_country = _first_present(
        _get_stripe_data_value(type_data, "country"),
        _get_stripe_data_value(card_present_data, "country"),
        _get_stripe_data_value(interac_present_data, "country"),
        _get_stripe_data_value(generated_type_data, "country"),
    )
    payment_method_network = _first_present(
        _get_stripe_data_value(type_data, "network"),
        _get_stripe_data_value(type_networks, "preferred"),
        _get_stripe_data_value(card_present_data, "network"),
        _get_stripe_data_value(card_present_networks, "preferred"),
        _get_stripe_data_value(interac_present_data, "network"),
        _get_stripe_data_value(interac_present_networks, "preferred"),
        _get_stripe_data_value(generated_type_data, "network"),
        _get_stripe_data_value(generated_type_networks, "preferred"),
    )
    payment_method_issuer = _first_present(
        _get_stripe_data_value(card_present_data, "issuer"),
        _get_stripe_data_value(interac_present_data, "issuer"),
        _get_stripe_data_value(generated_type_data, "issuer"),
    )

    properties: dict[str, Any] = {}
    if payment_method_id:
        properties["stripe.payment_method_id"] = payment_method_id
    if payment_method_type:
        properties["payment_method_type"] = payment_method_type
    if payment_method_brand:
        properties["payment_method_brand"] = payment_method_brand
    if payment_method_display_brand:
        properties["payment_method_display_brand"] = payment_method_display_brand
    if payment_method_last4:
        properties["payment_method_last4"] = payment_method_last4
    if payment_method_fingerprint:
        properties["payment_method_fingerprint"] = payment_method_fingerprint
    if payment_method_funding:
        properties["payment_method_funding"] = payment_method_funding
    if payment_method_country:
        properties["payment_method_country"] = payment_method_country
    if payment_method_network:
        properties["payment_method_network"] = payment_method_network
    if payment_method_issuer:
        properties["payment_method_issuer"] = payment_method_issuer
    if _get_stripe_data_value(billing_details, "name"):
        properties["payment_method_billing_name"] = _get_stripe_data_value(billing_details, "name")
    if _get_stripe_data_value(billing_details, "email"):
        properties["payment_method_billing_email"] = _get_stripe_data_value(billing_details, "email")
    if _get_stripe_data_value(billing_address, "country"):
        properties["payment_method_billing_country"] = _get_stripe_data_value(billing_address, "country")
    if _get_stripe_data_value(billing_address, "postal_code"):
        properties["payment_method_billing_postal_code"] = _get_stripe_data_value(billing_address, "postal_code")
    if _get_stripe_data_value(link_data, "email"):
        properties["payment_method_link_email"] = _get_stripe_data_value(link_data, "email")

    return properties


def _build_setup_intent_failure_properties(
    payload: Mapping[str, Any],
    *,
    allow_stripe_lookup: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    setup_intent_id = _extract_stripe_object_id(payload)
    setup_intent_data: dict[str, Any] = {}
    last_setup_error_data = _coerce_metadata_dict(payload.get("last_setup_error"))

    payment_method_id = _extract_stripe_object_id(payload.get("payment_method"))
    payment_method_data = _coerce_metadata_dict(payload.get("payment_method"))
    error_payment_method_data = _coerce_metadata_dict(_get_stripe_data_value(last_setup_error_data, "payment_method"))
    if not payment_method_data and error_payment_method_data:
        payment_method_data = error_payment_method_data
    if payment_method_id is None:
        payment_method_id = _extract_stripe_object_id(error_payment_method_data) or _extract_stripe_object_id(
            payment_method_data
        )

    customer_id_from_payload = _extract_stripe_object_id(payload.get("customer"))
    payment_method_customer_id = _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer"))
    payment_method_missing_customer = not customer_id_from_payload and not payment_method_customer_id

    if allow_stripe_lookup and setup_intent_id and (
        not last_setup_error_data or not payment_method_data or payment_method_missing_customer
    ):
        setup_intent_data = _retrieve_setup_intent_data(setup_intent_id)
        if not last_setup_error_data:
            last_setup_error_data = _coerce_metadata_dict(_get_stripe_data_value(setup_intent_data, "last_setup_error"))
        if payment_method_id is None:
            payment_method_id = _extract_stripe_object_id(_get_stripe_data_value(setup_intent_data, "payment_method"))
        if not payment_method_data:
            payment_method_data = _coerce_metadata_dict(_get_stripe_data_value(last_setup_error_data, "payment_method"))
        if not payment_method_data:
            payment_method_data = _coerce_metadata_dict(_get_stripe_data_value(setup_intent_data, "payment_method"))

    setup_intent_customer_id = _extract_stripe_object_id(_get_stripe_data_value(setup_intent_data, "customer"))
    payment_method_customer_id = _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer"))
    payment_method_missing_customer = (
        not customer_id_from_payload
        and not payment_method_customer_id
        and not setup_intent_customer_id
    )
    if allow_stripe_lookup and payment_method_id and (not payment_method_data or payment_method_missing_customer):
        retrieved_payment_method_data = _retrieve_payment_method_data(payment_method_id)
        if retrieved_payment_method_data:
            payment_method_data = retrieved_payment_method_data

    customer_id = _first_present(
        customer_id_from_payload,
        _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer")),
        setup_intent_customer_id,
    )

    failure_type = _get_stripe_data_value(last_setup_error_data, "type")
    failure_message = _get_stripe_data_value(last_setup_error_data, "message")
    failure_code = _get_stripe_data_value(last_setup_error_data, "code")
    decline_code = _get_stripe_data_value(last_setup_error_data, "decline_code")
    network_decline_code = _get_stripe_data_value(last_setup_error_data, "network_decline_code")
    network_advice_code = _get_stripe_data_value(last_setup_error_data, "network_advice_code")
    failure_reason = _first_present(
        failure_message,
        decline_code,
        network_decline_code,
        failure_code,
        failure_type,
    )

    payment_method_types = payload.get("payment_method_types")
    fallback_payment_method_type = None
    if isinstance(payment_method_types, list) and payment_method_types:
        fallback_payment_method_type = payment_method_types[0]

    properties: dict[str, Any] = {
        "stripe.setup_intent_id": setup_intent_id,
        "stripe.customer_id": customer_id,
        "stripe.payment_method_id": payment_method_id,
        "status": payload.get("status"),
        "usage": payload.get("usage"),
        "livemode": bool(payload.get("livemode")),
    }

    if failure_reason:
        properties["failure_reason"] = failure_reason
    if failure_message:
        properties["failure_message"] = failure_message
    if failure_code:
        properties["failure_code"] = failure_code
    if decline_code:
        properties["decline_code"] = decline_code
    if network_decline_code:
        properties["network_decline_code"] = network_decline_code
    if network_advice_code:
        properties["network_advice_code"] = network_advice_code
    if failure_type:
        properties["failure_type"] = failure_type

    properties.update(
        _extract_payment_method_analytics_properties(
            payment_method_data,
            fallback_type=fallback_payment_method_type,
        )
    )

    metadata = _coerce_metadata_dict(payload.get("metadata"))
    if metadata.get("gobii_event_id"):
        properties["gobii_event_id"] = metadata.get("gobii_event_id")

    properties = {key: value for key, value in properties.items() if value not in (None, "")}
    return properties, payment_method_data, customer_id


def _build_setup_intent_failure_properties_fallback(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    last_setup_error_data = _coerce_metadata_dict(payload.get("last_setup_error"))
    error_payment_method_data = _coerce_metadata_dict(_get_stripe_data_value(last_setup_error_data, "payment_method"))
    payment_method_types = payload.get("payment_method_types")
    payment_method_type = None
    if isinstance(payment_method_types, list) and payment_method_types:
        payment_method_type = payment_method_types[0]

    payment_method_id = _extract_stripe_object_id(payload.get("payment_method"))
    customer_id = _extract_stripe_object_id(payload.get("customer"))
    if customer_id is None:
        customer_id = _extract_stripe_object_id(_get_stripe_data_value(error_payment_method_data, "customer"))

    failure_type = _get_stripe_data_value(last_setup_error_data, "type")
    failure_message = _get_stripe_data_value(last_setup_error_data, "message")
    failure_code = _get_stripe_data_value(last_setup_error_data, "code")
    decline_code = _get_stripe_data_value(last_setup_error_data, "decline_code")
    failure_reason = _first_present(failure_message, decline_code, failure_code, failure_type)

    properties = {
        "stripe.setup_intent_id": _extract_stripe_object_id(payload),
        "stripe.customer_id": customer_id,
        "stripe.payment_method_id": payment_method_id,
        "status": payload.get("status"),
        "usage": payload.get("usage"),
        "livemode": bool(payload.get("livemode")),
        "failure_reason": failure_reason,
        "failure_message": failure_message,
        "failure_code": failure_code,
        "decline_code": decline_code,
        "failure_type": failure_type,
        "payment_method_type": payment_method_type,
    }
    return (
        {key: value for key, value in properties.items() if value not in (None, "")},
        {},
        customer_id,
    )


def _safe_build_setup_intent_failure_properties(
    payload: Mapping[str, Any],
    *,
    allow_stripe_lookup: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    try:
        return _build_setup_intent_failure_properties(
            payload,
            allow_stripe_lookup=allow_stripe_lookup,
        )
    except Exception:
        logger.exception(
            "Failed to build full setup intent analytics properties for setup intent %s; using fallback payload",
            payload.get("id"),
        )
        return _build_setup_intent_failure_properties_fallback(payload)


def _build_setup_intent_success_properties(
    payload: Mapping[str, Any],
    *,
    allow_stripe_lookup: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    setup_intent_id = _extract_stripe_object_id(payload)
    setup_intent_data: dict[str, Any] = {}

    payment_method_id = _extract_stripe_object_id(payload.get("payment_method"))
    payment_method_data = _coerce_metadata_dict(payload.get("payment_method"))

    customer_id_from_payload = _extract_stripe_object_id(payload.get("customer"))
    payment_method_customer_id = _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer"))
    payment_method_missing_customer = not customer_id_from_payload and not payment_method_customer_id

    if allow_stripe_lookup and setup_intent_id and (payment_method_missing_customer or payment_method_id is None):
        setup_intent_data = _retrieve_setup_intent_data(setup_intent_id)
        if payment_method_id is None:
            payment_method_id = _extract_stripe_object_id(_get_stripe_data_value(setup_intent_data, "payment_method"))
        if not payment_method_data:
            payment_method_data = _coerce_metadata_dict(_get_stripe_data_value(setup_intent_data, "payment_method"))

    setup_intent_customer_id = _extract_stripe_object_id(_get_stripe_data_value(setup_intent_data, "customer"))
    payment_method_customer_id = _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer"))
    payment_method_missing_customer = (
        not customer_id_from_payload
        and not payment_method_customer_id
        and not setup_intent_customer_id
    )
    if allow_stripe_lookup and payment_method_id and (not payment_method_data or payment_method_missing_customer):
        retrieved_payment_method_data = _retrieve_payment_method_data(payment_method_id)
        if retrieved_payment_method_data:
            payment_method_data = retrieved_payment_method_data

    customer_id = _first_present(
        customer_id_from_payload,
        _extract_stripe_object_id(_get_stripe_data_value(payment_method_data, "customer")),
        setup_intent_customer_id,
    )

    payment_method_types = payload.get("payment_method_types")
    fallback_payment_method_type = None
    if isinstance(payment_method_types, list) and payment_method_types:
        fallback_payment_method_type = payment_method_types[0]

    properties: dict[str, Any] = {
        "stripe.setup_intent_id": setup_intent_id,
        "stripe.customer_id": customer_id,
        "stripe.payment_method_id": payment_method_id,
        "status": payload.get("status"),
        "usage": payload.get("usage"),
        "livemode": bool(payload.get("livemode")),
    }

    properties.update(
        _extract_payment_method_analytics_properties(
            payment_method_data,
            fallback_type=fallback_payment_method_type,
        )
    )
    if fallback_payment_method_type and "payment_method_type" not in properties:
        properties["payment_method_type"] = fallback_payment_method_type

    metadata = _coerce_metadata_dict(payload.get("metadata"))
    if metadata.get("gobii_event_id"):
        properties["gobii_event_id"] = metadata.get("gobii_event_id")

    properties = {key: value for key, value in properties.items() if value not in (None, "")}
    return properties, payment_method_data, customer_id


def _build_setup_intent_success_properties_fallback(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    payment_method_types = payload.get("payment_method_types")
    payment_method_type = None
    if isinstance(payment_method_types, list) and payment_method_types:
        payment_method_type = payment_method_types[0]

    properties = {
        "stripe.setup_intent_id": _extract_stripe_object_id(payload),
        "stripe.customer_id": _extract_stripe_object_id(payload.get("customer")),
        "stripe.payment_method_id": _extract_stripe_object_id(payload.get("payment_method")),
        "status": payload.get("status"),
        "usage": payload.get("usage"),
        "livemode": bool(payload.get("livemode")),
        "payment_method_type": payment_method_type,
    }

    metadata = _coerce_metadata_dict(payload.get("metadata"))
    if metadata.get("gobii_event_id"):
        properties["gobii_event_id"] = metadata.get("gobii_event_id")

    return (
        {key: value for key, value in properties.items() if value not in (None, "")},
        {},
        _extract_stripe_object_id(payload.get("customer")),
    )


def _safe_build_setup_intent_success_properties(
    payload: Mapping[str, Any],
    *,
    allow_stripe_lookup: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    try:
        return _build_setup_intent_success_properties(
            payload,
            allow_stripe_lookup=allow_stripe_lookup,
        )
    except Exception:
        logger.exception(
            "Failed to build full setup intent success analytics properties for setup intent %s; using fallback payload",
            payload.get("id"),
        )
        return _build_setup_intent_success_properties_fallback(payload)


def _get_subscription_items_data(source: Any) -> list:
    if isinstance(source, Mapping):
        items_source = source.get("items")
    else:
        items_source = getattr(source, "items", None)

    if isinstance(items_source, Mapping):
        data = items_source.get("data") or []
    else:
        data = getattr(items_source, "data", None) or []

    if data is None:
        return []
    return list(data)


def _get_quantity_for_price(source_data: Any, price_id: str) -> int:
    if not price_id:
        return 0

    for item in _get_subscription_items_data(source_data):
        if isinstance(item, Mapping):
            price = item.get("price") or {}
            item_price_id = price.get("id")
            quantity = item.get("quantity")
        else:
            price = getattr(item, "price", None)
            item_price_id = getattr(price, "id", None) if price is not None else None
            quantity = getattr(item, "quantity", None)

        if item_price_id != price_id:
            continue

        try:
            return int(quantity or 0)
        except (TypeError, ValueError):
            return 0

    return 0


def _sync_dedicated_ip_allocations(owner, owner_type: str, source_data: Any, stripe_settings) -> None:
    if owner is None:
        return

    if owner_type == "user":
        price_id = getattr(stripe_settings, "startup_dedicated_ip_price_id", "")
    else:
        price_id = getattr(stripe_settings, "org_team_dedicated_ip_price_id", "")

    if not price_id:
        return

    desired_qty = max(_get_quantity_for_price(source_data, price_id), 0)
    current_qty = DedicatedProxyService.allocated_proxies(owner).count()

    if desired_qty == current_qty:
        return

    if desired_qty > current_qty:
        missing = desired_qty - current_qty
        allocated = 0
        for _ in range(missing):
            try:
                DedicatedProxyService.allocate_proxy(owner)
                allocated += 1
            except DedicatedProxyUnavailableError:
                logger.warning(
                    "Insufficient dedicated proxies for owner %s; fulfilled %s of %s requested.",
                    getattr(owner, "id", None) or owner,
                    allocated,
                    missing,
                )
                break
    else:
        release_limit = current_qty - desired_qty
        try:
            DedicatedProxyService.release_for_owner(owner, limit=release_limit)
        except Exception:
            logger.exception(
                "Failed to release surplus dedicated proxies for owner %s",
                getattr(owner, "id", None) or owner,
            )

@receiver(user_signed_up)
def handle_user_signed_up(sender, request, user, **kwargs):
    logger.info(f"New user signed up: {user.email}")

    auth_properties = _build_auth_tracking_properties(
        request,
        sociallogin=kwargs.get("sociallogin"),
        default_to_email=True,
    )
    request.session['show_signup_tracking'] = True
    request.session["signup_auth_method"] = auth_properties.get("auth_method", "email")
    auth_provider = auth_properties.get("auth_provider", "")
    if auth_provider:
        request.session["signup_auth_provider"] = auth_provider
    else:
        request.session.pop("signup_auth_provider", None)
    client_ip = _safe_client_ip(request)

    # Example: fire off an analytics event
    try:
        traits = {
            'first_name' : user.first_name or '',
            'last_name'  : user.last_name  or '',
            'email'      : user.email,
            'username'   : user.username or '',
            'date_joined': user.date_joined.isoformat(),
            'plan': PlanNames.FREE,
        }

        def _decode_cookie_value(raw: str | None) -> str:
            if not raw:
                return ''
            try:
                decoded = unquote(raw)
            except Exception:
                decoded = raw
            return decoded.strip().strip('"')

        utm_first_payload: dict[str, str] = {}
        utm_first_cookie = request.COOKIES.get('__utm_first')
        if utm_first_cookie:
            try:
                utm_first_payload = json.loads(utm_first_cookie)
            except json.JSONDecodeError:
                try:
                    utm_first_payload = json.loads(unquote(utm_first_cookie))
                except json.JSONDecodeError:
                    logger.exception("Failed to parse __utm_first cookie; Content: %s", utm_first_cookie)
                    utm_first_payload = {}

        current_touch = {
            utm_key: request.COOKIES.get(utm_key, '')
            for utm_key in UTM_MAPPING.values()
        }

        first_touch = {}
        for utm_key in UTM_MAPPING.values():
            preserved_value = utm_first_payload.get(utm_key)
            current_value = current_touch.get(utm_key)
            if preserved_value:
                first_touch[utm_key] = preserved_value
            elif current_value:
                first_touch[utm_key] = current_value

        last_touch = {k: v for k, v in current_touch.items() if v}

        session_first_touch = request.session.get("utm_first_touch") or {}
        session_last_touch = request.session.get("utm_last_touch") or {}
        if session_first_touch:
            for key, value in session_first_touch.items():
                if value and key not in first_touch:
                    first_touch[key] = value
        if session_last_touch:
            merged_last_touch = {k: v for k, v in session_last_touch.items() if v}
            merged_last_touch.update(last_touch)
            last_touch = merged_last_touch

        click_first_payload: dict[str, str] = {}
        click_first_cookie = request.COOKIES.get('__click_first')
        if click_first_cookie:
            try:
                click_first_payload = json.loads(click_first_cookie)
            except json.JSONDecodeError:
                try:
                    click_first_payload = json.loads(unquote(click_first_cookie))
                except json.JSONDecodeError:
                    logger.exception("Failed to parse __click_first cookie; Content: %s", click_first_cookie)
                    click_first_payload = {}

        current_click = {
            key: request.COOKIES.get(key, '')
            for key in CLICK_ID_PARAMS
        }

        first_click: dict[str, str] = {}
        for key in CLICK_ID_PARAMS:
            preserved = click_first_payload.get(key)
            current_val = current_click.get(key)
            if preserved:
                first_click[key] = preserved
            elif current_val:
                first_click[key] = current_val

        last_click = {k: v for k, v in current_click.items() if v}

        session_click_first = request.session.get("click_ids_first") or {}
        session_click_last = request.session.get("click_ids_last") or {}
        if session_click_first:
            for key, value in session_click_first.items():
                if value and key not in first_click:
                    first_click[key] = value
        if session_click_last:
            merged_last_click = {k: v for k, v in session_click_last.items() if v}
            merged_last_click.update(last_click)
            last_click = merged_last_click

        landing_first_cookie = _decode_cookie_value(request.COOKIES.get('__landing_first'))
        landing_last_cookie = _decode_cookie_value(request.COOKIES.get('landing_code'))
        landing_first = _decode_cookie_value(request.session.get('landing_code_first')) or landing_first_cookie
        landing_last = _decode_cookie_value(request.session.get('landing_code_last')) or landing_last_cookie or landing_first

        def _parse_session_timestamp(raw_value: str | None) -> datetime | None:
            if not raw_value:
                return None
            parsed = parse_datetime(raw_value)
            if parsed is None:
                return None
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone=dt_timezone.utc)
            return parsed

        first_touch_at = _parse_session_timestamp(request.session.get('landing_first_seen_at')) or timezone.now()
        last_touch_at = _parse_session_timestamp(request.session.get('landing_last_seen_at')) or timezone.now()

        fbc_cookie = _decode_cookie_value(request.COOKIES.get('_fbc'))
        fbp_cookie = _decode_cookie_value(request.COOKIES.get('_fbp'))
        fbclid_cookie = _decode_cookie_value(request.COOKIES.get('fbclid'))
        fbclid_session = request.session.get("fbclid_last") or request.session.get("fbclid_first")
        if not fbclid_cookie and fbclid_session:
            fbclid_cookie = fbclid_session

        first_referrer = _decode_cookie_value(request.COOKIES.get('first_referrer')) or (request.META.get('HTTP_REFERER') or '')
        last_referrer = _decode_cookie_value(request.COOKIES.get('last_referrer')) or (request.META.get('HTTP_REFERER') or first_referrer)
        first_path = _decode_cookie_value(request.COOKIES.get('first_path')) or request.get_full_path()
        last_path = _decode_cookie_value(request.COOKIES.get('last_path')) or request.get_full_path()

        segment_anonymous_id = _decode_cookie_value(request.COOKIES.get('ajs_anonymous_id'))
        ga_client_id = (
            _decode_cookie_value(request.POST.get("uga"))
            or _decode_cookie_value(request.COOKIES.get(SIGNUP_GA_CLIENT_COOKIE_NAME))
            or _decode_cookie_value(request.COOKIES.get('_ga'))
        )

        # ── Referral tracking ──────────────────────────────────────────
        # Direct referral: ?ref=<code> captured into session
        referrer_code = _decode_cookie_value(request.session.get('referrer_code', ''))
        # Template share: when user signed up after viewing a shared agent template
        signup_template_code = _decode_cookie_value(request.session.get('signup_template_code', ''))

        traits.update({f'{k}_first': v for k, v in first_touch.items()})
        if last_touch:
            traits.update({f'{k}_last': v for k, v in last_touch.items()})
        traits.update({f'{k}_first': v for k, v in first_click.items()})
        if last_click:
            traits.update({f'{k}_last': v for k, v in last_click.items()})
        if landing_first:
            traits['landing_code_first'] = landing_first
        if landing_last:
            traits['landing_code_last'] = landing_last
        if fbc_cookie:
            traits['fbc'] = fbc_cookie
        if fbclid_cookie:
            traits['fbclid'] = fbclid_cookie
        if first_referrer:
            traits['first_referrer'] = first_referrer
        if last_referrer:
            traits['last_referrer'] = last_referrer
        if first_path:
            traits['first_landing_path'] = first_path
        if last_path:
            traits['last_landing_path'] = last_path
        if segment_anonymous_id:
            traits['segment_anonymous_id'] = segment_anonymous_id
        if ga_client_id:
            traits['ga_client_id'] = ga_client_id

        try:
            UserAttribution.objects.update_or_create(
                user=user,
                defaults={
                    'utm_source_first': first_touch.get('utm_source', ''),
                    'utm_medium_first': first_touch.get('utm_medium', ''),
                    'utm_campaign_first': first_touch.get('utm_campaign', ''),
                    'utm_content_first': first_touch.get('utm_content', ''),
                    'utm_term_first': first_touch.get('utm_term', ''),
                    'utm_source_last': last_touch.get('utm_source', ''),
                    'utm_medium_last': last_touch.get('utm_medium', ''),
                    'utm_campaign_last': last_touch.get('utm_campaign', ''),
                    'utm_content_last': last_touch.get('utm_content', ''),
                    'utm_term_last': last_touch.get('utm_term', ''),
                    'landing_code_first': landing_first,
                    'landing_code_last': landing_last,
                    'fbclid': fbclid_cookie,
                    'fbc': fbc_cookie,
                    'gclid_first': first_click.get('gclid', ''),
                    'gclid_last': last_click.get('gclid', ''),
                    'gbraid_first': first_click.get('gbraid', ''),
                    'gbraid_last': last_click.get('gbraid', ''),
                    'wbraid_first': first_click.get('wbraid', ''),
                    'wbraid_last': last_click.get('wbraid', ''),
                    'msclkid_first': first_click.get('msclkid', ''),
                    'msclkid_last': last_click.get('msclkid', ''),
                    'ttclid_first': first_click.get('ttclid', ''),
                    'ttclid_last': last_click.get('ttclid', ''),
                    'rdt_cid_first': first_click.get('rdt_cid', ''),
                    'rdt_cid_last': last_click.get('rdt_cid', ''),
                    'li_fat_id_first': first_click.get('li_fat_id', ''),
                    'li_fat_id_last': last_click.get('li_fat_id', ''),
                    'first_referrer': first_referrer,
                    'last_referrer': last_referrer,
                    'first_landing_path': first_path,
                    'last_landing_path': last_path,
            'segment_anonymous_id': segment_anonymous_id,
            'ga_client_id': ga_client_id,
            'first_touch_at': first_touch_at,
            'last_touch_at': last_touch_at,
            'last_client_ip': client_ip,
            'last_user_agent': request.META.get('HTTP_USER_AGENT', ''),
            'fbp': fbp_cookie,
            'referrer_code': referrer_code,
            'signup_template_code': signup_template_code,
        },
    )
        except Exception:
            logger.exception("Failed to persist user attribution for user %s", user.id)

        capture_request_identity_signals_and_attribution(
            user,
            request,
            source=SIGNAL_SOURCE_SIGNUP,
            include_fpjs=True,
        )
        evaluate_user_trial_eligibility(
            user,
            assessment_source=SIGNAL_SOURCE_SIGNUP,
        )

        # ── Handle Referral ────────────────────────────────────────────
        # Process referral signup - identifies referrer and (TODO) grants credits
        if referrer_code or signup_template_code:
            try:
                ReferralService.process_signup_referral(
                    new_user=user,
                    referrer_code=referrer_code,
                    template_code=signup_template_code,
                )
            except Exception:
                logger.exception(
                    "Failed to process referral for user %s (ref=%s, template=%s)",
                    user.id,
                    referrer_code or '(none)',
                    signup_template_code or '(none)',
                )

        Analytics.identify(
            user_id=str(user.id),
            traits=traits,
        )

        # ── 2. event-specific properties & last-touch UTMs ──────
        event_id = f'reg-{uuid.uuid4()}'
        request.session['signup_event_id'] = event_id
        request.session['signup_user_id'] = str(user.id)
        normalized_email = (user.email or '').strip().lower()
        if normalized_email:
            request.session['signup_email_hash'] = hashlib.sha256(normalized_email.encode('utf-8')).hexdigest()
        else:
            request.session.pop('signup_email_hash', None)

        event_properties = {
            'plan': PlanNames.FREE,
            'date_joined': user.date_joined.isoformat(),
            **{f'{k}_first': v for k, v in first_touch.items()},
            **{f'{k}_last': v for k, v in last_touch.items()},
            **{f'{k}_first': v for k, v in first_click.items()},
            **{f'{k}_last': v for k, v in last_click.items()},
        }
        event_properties.update(auth_properties)

        if landing_first:
            event_properties['landing_code_first'] = landing_first
        if landing_last:
            event_properties['landing_code_last'] = landing_last
        if fbc_cookie:
            event_properties['fbc'] = fbc_cookie
        if fbclid_cookie:
            event_properties['fbclid'] = fbclid_cookie
        if first_referrer:
            event_properties['first_referrer'] = first_referrer
        if last_referrer:
            event_properties['last_referrer'] = last_referrer
        if first_path:
            event_properties['first_landing_path'] = first_path
        if last_path:
            event_properties['last_landing_path'] = last_path
        if segment_anonymous_id:
            event_properties['segment_anonymous_id'] = segment_anonymous_id
        if ga_client_id:
            event_properties['ga_client_id'] = ga_client_id
        if fbc_cookie:
            event_properties['fbc'] = fbc_cookie
        if fbp_cookie:
            event_properties['fbp'] = fbp_cookie
        if fbclid_cookie:
            event_properties['fbclid'] = fbclid_cookie

        campaign_context = {}
        for key, utm_param in UTM_MAPPING.items():
            value = last_touch.get(utm_param) or first_touch.get(utm_param, '')
            if value:
                campaign_context[key] = value

        for key in CLICK_ID_PARAMS:
            value = last_click.get(key) or first_click.get(key, '')
            if value:
                campaign_context[key] = value

        if landing_last or landing_first:
            campaign_context['landing_code'] = landing_last or landing_first
        if last_referrer:
            campaign_context['referrer'] = last_referrer

        event_timestamp = timezone.now()
        event_timestamp_unix = int(event_timestamp.timestamp())
        event_timestamp_ms = int(event_timestamp.timestamp() * 1000)

        Analytics.track(
            user_id=str(user.id),
            event=AnalyticsEvent.SIGNUP,
            properties=event_properties,
            context={
                'campaign': campaign_context,
                'userAgent': request.META.get('HTTP_USER_AGENT', ''),
            },
            ip=None,
            message_id=event_id,          # use same ID in Facebook/Reddit CAPI
            timestamp=event_timestamp
        )

        if not getattr(settings, 'GOBII_PROPRIETARY_MODE', False):
            logger.debug("Skipping conversion API enqueue because proprietary mode is disabled.")
            logger.info("Analytics tracking successful for signup.")
            return

        def enqueue_conversion_tasks():
            marketing_properties = {
                k: v
                for k, v in event_properties.items()
                if v not in (None, '', [])
            }
            marketing_properties.update(
                {
                    'event_id': event_id,
                    'event_time': event_timestamp_unix,
                }
            )
            registration_value = float(getattr(settings, "CAPI_REGISTRATION_VALUE", 0.0) or 0.0)
            marketing_properties["value"] = registration_value
            marketing_properties.setdefault("currency", "USD")
            additional_click_ids = {
                key: value
                for key in CLICK_ID_PARAMS
                if (value := (last_click.get(key) or first_click.get(key)))
            }
            marketing_context = extract_click_context(request)
            if additional_click_ids:
                marketing_context['click_ids'] = {
                    **additional_click_ids,
                    **(marketing_context.get('click_ids') or {}),
                }
            # Ensure fbc is present for Meta CAPI if we have fbclid from session/cookies
            # This improves Event Match Quality when user lands with fbclid but signs up
            # on a different page without fbclid in the URL
            click_ids = marketing_context.get('click_ids') or {}
            if not click_ids.get('fbc') and not fbc_cookie:
                # No fbc from cookies or extract_click_context, try to synthesize from fbclid
                stored_fbclid = fbclid_cookie  # includes session fallback from lines 750-753
                if stored_fbclid:
                    synthesized_fbc = f"fb.1.{event_timestamp_ms}.{stored_fbclid}"
                    click_ids['fbc'] = synthesized_fbc
                    click_ids['fbclid'] = stored_fbclid
                    marketing_context['click_ids'] = click_ids
                    record_fbc_synthesized(source="pages.signals.handle_user_signed_up")
                    # Persist so webhook events use the stored value instead of re-synthesizing
                    try:
                        UserAttribution.objects.filter(user=user).update(fbc=synthesized_fbc)
                    except Exception:
                        logger.warning("Failed to persist synthesized fbc for user %s", user.id, exc_info=True)
            elif fbc_cookie and not click_ids.get('fbc'):
                # fbc exists in cookie but wasn't captured by extract_click_context
                click_ids['fbc'] = fbc_cookie
                if fbclid_cookie:
                    click_ids['fbclid'] = fbclid_cookie
                marketing_context['click_ids'] = click_ids
            utm_context = {
                **{f'{k}_first': v for k, v in first_touch.items() if v},
                **{f'{k}_last': v for k, v in last_touch.items() if v},
            }
            if utm_context:
                marketing_context['utm'] = {
                    **utm_context,
                    **(marketing_context.get('utm') or {}),
                }
            if campaign_context:
                marketing_context['campaign'] = campaign_context
            marketing_context['consent'] = True
            capi(
                user=user,
                event_name='CompleteRegistration',
                properties=marketing_properties,
                request=request,
                context=marketing_context,
            )

        transaction.on_commit(enqueue_conversion_tasks)

        logger.info("Analytics tracking successful for signup.")
    except Exception as e:
        logger.exception("Analytics tracking failed during signup.")

@receiver(email_confirmed)
def handle_email_confirmed(sender, request, email_address, **kwargs):
    user = getattr(email_address, "user", None)
    if not user:
        return
    if ReferralService.is_deferred_granting_enabled():
        if not settings.DEFERRED_REFERRAL_CREDITS_ENABLED:
            return
        ReferralService.check_and_grant_deferred_referral_credits(user)
    else:
        ReferralService.check_and_grant_immediate_referral_credits(user)

@receiver(user_logged_in)
def handle_user_logged_in(sender, request, user, **kwargs):
    logger.info(f"User logged in: {user.id} ({user.email})")

    try:
        auth_properties = _build_auth_tracking_properties(request)
        capture_request_identity_signals_and_attribution(
            user,
            request,
            source=SIGNAL_SOURCE_LOGIN,
            include_fpjs=False,
        )
        Analytics.identify(user.id, {
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'email': user.email,
            'username': user.username or '',
            'date_joined': user.date_joined,
        })
        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.LOGGED_IN,
            source=AnalyticsSource.WEB,
            properties=auth_properties,
        )
        logger.info("Analytics tracking successful for login.")
    except Exception:
        logger.exception("Analytics tracking failed during login.")

@receiver(user_logged_out)
def handle_user_logged_out(sender, request, user, **kwargs):
    logger.info(f"User logged out: {user.id} ({user.email})")

    try:
        Analytics.track_event(
            user_id=user.id,
            event=AnalyticsEvent.LOGGED_OUT,
            source=AnalyticsSource.WEB,
            properties={}
        )
        logger.info("Analytics tracking successful for logout.")
    except Exception:
        logger.exception("Analytics tracking failed during logout.")


@djstripe_receiver(["checkout.session.completed", "checkout.session.expired"])
def handle_checkout_session_event(event, **kwargs):
    """Emit purchase AddPaymentInfo and clear transient checkout metadata at terminal session states."""
    payload = _coerce_metadata_dict(getattr(event, "data", {}).get("object"))
    if payload.get("object") != "checkout.session":
        return

    status = stripe_status()
    if not status.enabled:
        logger.info("Stripe disabled; ignoring checkout session webhook %s", payload.get("id"))
        return

    stripe_key = PaymentsHelper.get_stripe_key()
    if not stripe_key:
        logger.warning("Stripe key unavailable; ignoring checkout session webhook %s", payload.get("id"))
        return

    stripe.api_key = stripe_key

    customer_id = _get_stripe_data_value(payload, "customer")
    if isinstance(customer_id, Mapping):
        customer_id = customer_id.get("id")
    customer_id = str(customer_id or "").strip()

    metadata = _coerce_metadata_dict(payload.get("metadata"))
    expected_event_id = str(metadata.get("gobii_event_id") or "").strip()
    checkout_session_id = _extract_stripe_object_id(payload)
    stripe_subscription_id = _extract_stripe_object_id(_get_stripe_data_value(payload, "subscription"))
    event_type = getattr(event, "type", "")
    if event_type == "checkout.session.completed":
        mark_trial_promo_redemption_from_checkout_session(
            checkout_session_id=checkout_session_id,
            status=TrialPromoRedemptionStatusChoices.CHECKOUT_COMPLETED,
            stripe_subscription_id=stripe_subscription_id,
        )
    elif event_type == "checkout.session.expired":
        mark_trial_promo_redemption_from_checkout_session(
            checkout_session_id=checkout_session_id,
            status=TrialPromoRedemptionStatusChoices.CHECKOUT_EXPIRED,
            stripe_subscription_id=stripe_subscription_id,
        )

    if not customer_id or not expected_event_id:
        return

    if event_type == "checkout.session.completed":
        try:
            _emit_add_payment_info_for_completed_checkout_session(
                payload=payload,
                customer_id=customer_id,
            )
        except Exception:
            logger.exception(
                "Failed to enqueue AddPaymentInfo for checkout session %s",
                payload.get("id"),
            )

    try:
        _clear_customer_checkout_context_if_matches(
            customer_id=customer_id,
            expected_event_id=expected_event_id,
        )
    except stripe.error.StripeError as exc:
        logger.warning(
            "Failed to clear checkout customer context for customer %s after session %s: %s",
            customer_id,
            payload.get("id"),
            exc,
        )


@djstripe_receiver(["invoice.payment_failed"])
def handle_invoice_payment_failed(event, **kwargs):
    """Emit analytics when Stripe fails to collect payment for an invoice."""
    with tracer.start_as_current_span("handle_invoice_payment_failed") as span:
        payload = event.data.get("object", {}) or {}
        if payload.get("object") != "invoice":
            span.add_event("unexpected_object", {"object": payload.get("object")})
            logger.info("Invoice payment failed webhook received non-invoice payload")
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event("stripe_disabled")
            logger.info("Stripe disabled; ignoring invoice payment failed webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event("stripe_key_missing")
            logger.warning("Stripe key unavailable; ignoring invoice payment failed webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        payload = _coerce_invoice_payload(payload)

        invoice = None
        try:
            invoice = Invoice.sync_from_stripe_data(payload)
        except Exception:
            span.add_event("invoice_sync_failed")
            logger.exception("Failed to sync invoice %s from webhook", payload.get("id"))

        owner, owner_type, _organization_billing, customer_id = _resolve_invoice_owner(invoice, payload)

        if owner_type:
            span.set_attribute("invoice.owner.type", owner_type)
        if owner:
            span.set_attribute("invoice.owner.id", str(getattr(owner, "id", "")))
        if not owner:
            span.add_event("owner_not_found", {"customer.id": customer_id})

        subscription_id = _extract_subscription_id(payload, invoice)
        lines = _invoice_lines(payload)
        plan_value = _extract_plan_from_lines(lines)

        properties = _safe_build_invoice_properties(
            payload,
            invoice,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan_value=plan_value,
            lines=lines,
            allow_failure_detail_lookup=True,
        )
        properties = Analytics.with_org_properties(
            properties,
            organization=owner if owner_type == "organization" else None,
            organization_flag=owner_type == "organization",
        )
        properties.setdefault("trial_conversion_invoice", False)

        subscription_obj = getattr(invoice, "subscription", None) if invoice else None
        subscription_data = getattr(subscription_obj, "stripe_data", {}) if subscription_obj else {}
        if not isinstance(subscription_data, Mapping):
            subscription_data = {}

        billing_reason = payload.get("billing_reason")
        line_period_start_dt = _line_period_start(lines)
        trial_end_dt = _coerce_datetime(_get_stripe_data_value(subscription_data, "trial_end"))
        if trial_end_dt is None:
            trial_end_dt = _coerce_datetime(_get_stripe_data_value(subscription_obj, "trial_end"))
        subscription_current_period_start_dt = _coerce_datetime(
            _get_stripe_data_value(subscription_data, "current_period_start")
        )
        if subscription_current_period_start_dt is None:
            subscription_current_period_start_dt = _coerce_datetime(
                _get_stripe_data_value(subscription_obj, "current_period_start")
            )

        attempt_count_raw = payload.get("attempt_count")
        try:
            attempt_count = int(attempt_count_raw) if attempt_count_raw is not None else None
        except (TypeError, ValueError):
            attempt_count = None
        final_attempt = _is_final_payment_attempt(payload)

        subscription_status = _get_stripe_data_value(subscription_data, "status")
        if subscription_status is None:
            subscription_status = _get_stripe_data_value(subscription_obj, "status")
        if isinstance(subscription_status, str):
            subscription_status = subscription_status.strip().lower()
        else:
            subscription_status = None

        trial_conversion_invoice = is_trial_conversion_invoice(
            billing_reason=billing_reason,
            trial_end_dt=trial_end_dt,
            line_period_start_dt=line_period_start_dt,
            subscription_current_period_start_dt=subscription_current_period_start_dt,
            subscription_status=subscription_status,
        )
        properties["trial_conversion_invoice"] = trial_conversion_invoice

        if owner and owner_type and is_trial_conversion_failure(
            billing_reason=billing_reason,
            trial_end_dt=trial_end_dt,
            line_period_start_dt=line_period_start_dt,
            subscription_current_period_start_dt=subscription_current_period_start_dt,
            subscription_status=subscription_status,
            attempt_count=attempt_count,
        ):
            try:
                emit_billing_lifecycle_event(
                    TRIAL_CONVERSION_FAILED,
                    sender=handle_invoice_payment_failed,
                    payload=BillingLifecyclePayload(
                        owner_type=owner_type,
                        owner_id=str(getattr(owner, "id", "")),
                        actor_user_id=_resolve_actor_user_id(owner, owner_type),
                        subscription_id=str(subscription_id) if subscription_id else None,
                        invoice_id=str(payload.get("id", "")) or None,
                        stripe_event_id=str(getattr(event, "id", "")) or None,
                        subscription_status=subscription_status,
                        attempt_count=attempt_count,
                        final_attempt=final_attempt,
                        occurred_at=timezone.now(),
                        metadata=dict(properties),
                    ),
                )
            except Exception:
                logger.exception(
                    "Failed to emit trial conversion failure lifecycle event for invoice %s",
                    payload.get("id"),
                )

        marketing_event_name = None
        if owner_type == "user" and owner and subscription_id:
            if trial_conversion_invoice:
                if final_attempt:
                    marketing_event_name = TRIAL_CONVERSION_PAYMENT_FAILED_FINAL_EVENT
                else:
                    marketing_event_name = TRIAL_CONVERSION_PAYMENT_FAILED_EVENT
            elif not final_attempt:
                marketing_event_name = SUBSCRIPTION_PAYMENT_FAILED_EVENT

        if marketing_event_name:
            try:
                failed_amount = properties.get("amount_due")
                marketing_properties = {
                    "plan": plan_value,
                    "subscription_id": subscription_id,
                    "stripe.invoice_id": payload.get("id"),
                    "attempt_number": attempt_count,
                    "final_attempt": final_attempt,
                    "trial_conversion_invoice": trial_conversion_invoice,
                    "value": failed_amount,
                    "amount_due": failed_amount,
                    "currency": properties.get("currency"),
                }
                marketing_event_id = str(getattr(event, "id", "") or "").strip()
                if not marketing_event_id:
                    invoice_id = str(payload.get("id", "") or "").strip()
                    attempt_suffix = str(attempt_count) if attempt_count is not None else ""
                    marketing_event_id = ":".join(part for part in (invoice_id, attempt_suffix) if part)
                if marketing_event_id:
                    marketing_properties["event_id"] = marketing_event_id
                marketing_properties = {
                    key: value
                    for key, value in marketing_properties.items()
                    if value not in (None, "")
                }

                metadata = _coerce_metadata_dict(subscription_data.get("metadata"))
                if not metadata:
                    metadata = _coerce_metadata_dict(payload.get("metadata"))

                marketing_context = _build_marketing_context_from_user(owner)
                checkout_source_url = metadata.get("checkout_source_url")
                if checkout_source_url:
                    marketing_context["page"] = {"url": checkout_source_url}

                capi(
                    user=owner,
                    event_name=marketing_event_name,
                    properties=marketing_properties,
                    request=None,
                    context=marketing_context,
                    provider_targets=AD_CAPI_PROVIDER_TARGETS,
                )
            except Exception:
                logger.exception(
                    "Failed to emit payment failure marketing event for invoice %s",
                    payload.get("id"),
                )

        try:
            if owner_type == "user" and owner:
                track_user_id = getattr(owner, "id", None)

                Analytics.track_event(
                    user_id=track_user_id,
                    event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            elif owner_type == "organization" and owner:
                track_user_id = getattr(owner, "created_by_id", None)
                if track_user_id:
                    Analytics.track_event(
                        user_id=track_user_id,
                        event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
                elif customer_id:
                    Analytics.track_event_anonymous(
                        anonymous_id=str(customer_id),
                        event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
            elif customer_id:
                Analytics.track_event_anonymous(
                    anonymous_id=str(customer_id),
                    event=AnalyticsEvent.BILLING_PAYMENT_FAILED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            else:
                span.add_event("analytics_skipped_no_actor")
                logger.info("Skipping analytics for invoice %s: no user or customer context", payload.get("id"))
        except Exception:
            span.add_event("analytics_failure")
            logger.exception("Failed to track invoice.payment_failed for invoice %s", payload.get("id"))


@djstripe_receiver(["setup_intent.setup_failed"])
def handle_setup_intent_setup_failed(event, **kwargs):
    """Emit analytics when Stripe cannot save a payment method with a SetupIntent."""
    with tracer.start_as_current_span("handle_setup_intent_setup_failed") as span:
        payload = event.data.get("object", {}) or {}
        if payload.get("object") != "setup_intent":
            span.add_event("unexpected_object", {"object": payload.get("object")})
            logger.info("Setup intent failed webhook received non-setup-intent payload")
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event("stripe_disabled")
            logger.info("Stripe disabled; ignoring setup intent failed webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event("stripe_key_missing")
            logger.warning("Stripe key unavailable; ignoring setup intent failed webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        properties, payment_method_data, customer_id = _safe_build_setup_intent_failure_properties(
            payload,
            allow_stripe_lookup=True,
        )
        owner, owner_type, _organization_billing, resolved_customer_id = _resolve_setup_intent_owner(
            payload,
            payment_method_data,
            customer_id=customer_id,
        )
        customer_id = resolved_customer_id or customer_id
        if customer_id and "stripe.customer_id" not in properties:
            properties["stripe.customer_id"] = customer_id

        if owner_type:
            span.set_attribute("setup_intent.owner.type", owner_type)
        if owner:
            span.set_attribute("setup_intent.owner.id", str(getattr(owner, "id", "")))
        if not owner:
            span.add_event(
                "owner_not_found",
                {
                    "customer.id": customer_id or "",
                    "payment_method.id": str(properties.get("stripe.payment_method_id", "") or ""),
                },
            )

        actor_user = _resolve_actor_user(owner, owner_type)
        properties.update(_build_actor_user_properties(actor_user))
        properties = Analytics.with_org_properties(
            properties,
            organization=owner if owner_type == "organization" else None,
            organization_flag=owner_type == "organization",
        )

        try:
            if actor_user and getattr(actor_user, "id", None):
                identify_traits = _build_analytics_identify_traits(actor_user)
                if identify_traits:
                    Analytics.identify(actor_user.id, identify_traits)
                Analytics.track_event(
                    user_id=actor_user.id,
                    event=AnalyticsEvent.PAYMENT_SETUP_INTENT_FAILED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            else:
                span.add_event("analytics_skipped_no_actor")
                logger.info(
                    "Skipping analytics for setup intent %s: no resolved user context",
                    payload.get("id"),
                )
        except Exception:
            span.add_event("analytics_failure")
            logger.exception("Failed to track setup_intent.setup_failed for setup intent %s", payload.get("id"))


@djstripe_receiver(["setup_intent.created"])
def handle_setup_intent_created(event, **kwargs):
    payload = event.data.get("object", {}) or {}
    if payload.get("object") != "setup_intent":
        return

    try:
        bind_setup_intent_checkout_context(
            customer_id=_extract_stripe_object_id(payload.get("customer")),
            setup_intent_id=_extract_stripe_object_id(payload),
            setup_intent_created_at=payload.get("created"),
        )
    except DatabaseError:
        logger.warning(
            "Failed to bind checkout context for setup intent %s",
            payload.get("id"),
            exc_info=True,
        )


@djstripe_receiver(["setup_intent.succeeded"])
def handle_setup_intent_succeeded(event, **kwargs):
    """Apply a newly saved payment method to the owner's active subscription."""
    with tracer.start_as_current_span("handle_setup_intent_succeeded") as span:
        payload = event.data.get("object", {}) or {}
        if payload.get("object") != "setup_intent":
            span.add_event("unexpected_object", {"object": payload.get("object")})
            logger.info("Setup intent succeeded webhook received non-setup-intent payload")
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event("stripe_disabled")
            logger.info("Stripe disabled; ignoring setup intent succeeded webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event("stripe_key_missing")
            logger.warning("Stripe key unavailable; ignoring setup intent succeeded webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        properties, payment_method_data, customer_id = _safe_build_setup_intent_success_properties(
            payload,
            allow_stripe_lookup=True,
        )
        payment_method_id = str(properties.get("stripe.payment_method_id") or "").strip() or None

        try:
            bind_setup_intent_checkout_context(
                customer_id=customer_id,
                setup_intent_id=_extract_stripe_object_id(payload),
                setup_intent_created_at=payload.get("created"),
            )
        except DatabaseError:
            logger.warning(
                "Failed to bind checkout context for setup intent %s before success handling",
                payload.get("id"),
                exc_info=True,
            )

        owner, owner_type, _organization_billing, customer_id = _resolve_setup_intent_owner(
            payload,
            payment_method_data,
            customer_id=customer_id,
        )
        customer_id = customer_id or _extract_stripe_object_id(payload.get("customer"))
        if customer_id and "stripe.customer_id" not in properties:
            properties["stripe.customer_id"] = customer_id
        if not owner:
            span.add_event("owner_not_found", {"customer.id": customer_id or ""})
            logger.info(
                "Unable to resolve owner for setup intent %s (customer=%s); skipping subscription payment method sync",
                payload.get("id"),
                customer_id,
            )
            return

        if owner_type:
            span.set_attribute("setup_intent.owner.type", owner_type)
        owner_id = getattr(owner, "id", None)
        if owner_id is not None:
            span.set_attribute("setup_intent.owner.id", str(owner_id))

        actor_user = _resolve_actor_user(owner, owner_type)
        properties.update(_build_actor_user_properties(actor_user))
        properties = Analytics.with_org_properties(
            properties,
            organization=owner if owner_type == "organization" else None,
            organization_flag=owner_type == "organization",
        )

        try:
            if actor_user and getattr(actor_user, "id", None):
                identify_traits = _build_analytics_identify_traits(actor_user)
                if identify_traits:
                    Analytics.identify(actor_user.id, identify_traits)
                Analytics.track_event(
                    user_id=actor_user.id,
                    event=AnalyticsEvent.PAYMENT_SETUP_INTENT_SUCCEEDED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            else:
                span.add_event("analytics_skipped_no_actor")
                logger.info(
                    "Skipping analytics for setup intent %s: no resolved user context",
                    payload.get("id"),
                )
        except Exception:
            span.add_event("analytics_failure")
            logger.exception("Failed to track setup_intent.succeeded for setup intent %s", payload.get("id"))

        if not payment_method_id:
            span.add_event("payment_method_missing")
            logger.info(
                "Setup intent %s succeeded without a payment method; skipping subscription update",
                payload.get("id"),
            )
            return

        try:
            _emit_add_payment_info_for_setup_intent(
                owner=owner,
                owner_type=owner_type,
                payload=payload,
            )
        except Exception:
            span.add_event("add_payment_info_emit_failed")
            logger.exception(
                "Failed to enqueue AddPaymentInfo for setup intent %s",
                payload.get("id"),
            )

        subscription = get_active_subscription(owner, sync_with_stripe=True)
        if subscription is None or not getattr(subscription, "id", None):
            span.add_event("subscription_not_found")
            logger.info(
                "No active subscription found for owner %s while syncing setup intent %s payment method",
                owner_id,
                payload.get("id"),
            )
            return

        current_default_payment_method = _extract_stripe_object_id(
            getattr(subscription, "default_payment_method_id", None)
            or _get_stripe_data_value(getattr(subscription, "stripe_data", None), "default_payment_method")
        )
        if current_default_payment_method == payment_method_id:
            span.add_event("subscription_already_current")
            return

        try:
            updated_subscription = stripe.Subscription.modify(
                subscription.id,
                default_payment_method=payment_method_id,
                idempotency_key=f"setup-intent-default-payment-method-{payload.get('id', '')}-{subscription.id}",
                api_key=stripe_key,
            )
        except stripe.error.StripeError:
            span.add_event("subscription_update_failed")
            logger.exception(
                "Failed to update default payment method for subscription %s from setup intent %s",
                subscription.id,
                payload.get("id"),
            )
            return

        sync_subscription_after_direct_update(updated_subscription)


@djstripe_receiver(["invoice.payment_succeeded"])
def handle_invoice_payment_succeeded(event, **kwargs):
    """Emit analytics when Stripe successfully collects payment for an invoice."""
    with tracer.start_as_current_span("handle_invoice_payment_succeeded") as span:
        payload = event.data.get("object", {}) or {}
        if payload.get("object") != "invoice":
            span.add_event("unexpected_object", {"object": payload.get("object")})
            logger.info("Invoice payment succeeded webhook received non-invoice payload")
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event("stripe_disabled")
            logger.info("Stripe disabled; ignoring invoice payment succeeded webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event("stripe_key_missing")
            logger.warning("Stripe key unavailable; ignoring invoice payment succeeded webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        payload = _coerce_invoice_payload(payload)

        invoice = None
        try:
            invoice = Invoice.sync_from_stripe_data(payload)
        except Exception:
            span.add_event("invoice_sync_failed")
            logger.exception("Failed to sync invoice %s from webhook", payload.get("id"))

        owner, owner_type, _organization_billing, customer_id = _resolve_invoice_owner(invoice, payload)

        if owner_type:
            span.set_attribute("invoice.owner.type", owner_type)
        if owner:
            span.set_attribute("invoice.owner.id", str(getattr(owner, "id", "")))
        if not owner:
            span.add_event("owner_not_found", {"customer.id": customer_id})

        subscription_id = _extract_subscription_id(payload, invoice)
        lines = _invoice_lines(payload)
        plan_value = _extract_plan_from_lines(lines)

        properties = _safe_build_invoice_properties(
            payload,
            invoice,
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan_value=plan_value,
            lines=lines,
        )

        properties = Analytics.with_org_properties(
            properties,
            organization=owner if owner_type == "organization" else None,
            organization_flag=owner_type == "organization",
        )

        try:
            if owner_type == "user" and owner:
                track_user_id = getattr(owner, "id", None)

                Analytics.track_event(
                    user_id=track_user_id,
                    event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            elif owner_type == "organization" and owner:
                track_user_id = getattr(owner, "created_by_id", None)
                if track_user_id:
                    Analytics.track_event(
                        user_id=track_user_id,
                        event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
                elif customer_id:
                    Analytics.track_event_anonymous(
                        anonymous_id=str(customer_id),
                        event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                        source=AnalyticsSource.API,
                        properties=properties,
                    )
            elif customer_id:
                Analytics.track_event_anonymous(
                    anonymous_id=str(customer_id),
                    event=AnalyticsEvent.BILLING_PAYMENT_SUCCEEDED,
                    source=AnalyticsSource.API,
                    properties=properties,
                )
            else:
                span.add_event("analytics_skipped_no_actor")
                logger.info("Skipping analytics for invoice %s: no user or customer context", payload.get("id"))
        except Exception:
            span.add_event("analytics_failure")
            logger.exception("Failed to track invoice.payment_succeeded for invoice %s", payload.get("id"))

        billing_reason = payload.get("billing_reason")
        subscription_obj = getattr(invoice, "subscription", None) if invoice else None
        subscription_data = getattr(subscription_obj, "stripe_data", {}) if subscription_obj else {}
        trial_end_dt = _coerce_datetime(_get_stripe_data_value(subscription_data, "trial_end"))
        if trial_end_dt is None:
            trial_end_dt = _coerce_datetime(_get_stripe_data_value(subscription_obj, "trial_end"))
        line_start_dt = _line_period_start(lines)
        subscription_current_period_start_dt = _coerce_datetime(
            _get_stripe_data_value(subscription_data, "current_period_start")
        )
        if subscription_current_period_start_dt is None:
            subscription_current_period_start_dt = _coerce_datetime(
                _get_stripe_data_value(subscription_obj, "current_period_start")
            )
        trial_conversion = is_trial_conversion_charge(
            billing_reason=billing_reason,
            trial_end_dt=trial_end_dt,
            line_period_start_dt=line_start_dt,
            subscription_current_period_start_dt=subscription_current_period_start_dt,
        )

        try:
            if trial_conversion and owner_type == "user" and owner:
                trial_properties = {
                    "plan": plan_value,
                    "subscription_id": subscription_id,
                    "stripe.invoice_id": payload.get("id"),
                }
                Analytics.track_event(
                    user_id=owner.id,
                    event=AnalyticsEvent.BILLING_TRIAL_CONVERTED,
                    source=AnalyticsSource.API,
                    properties=trial_properties,
                )
                traits = {"is_trial": False}
                if plan_value:
                    traits["plan"] = plan_value
                Analytics.identify(owner.id, traits)
        except Exception:
            logger.exception(
                "Failed to track trial conversion analytics for invoice %s",
                payload.get("id"),
            )

        try:
            subscription_status = str(_get_stripe_data_value(subscription_data, "status") or "").lower()
            trial_start_invoice = bool(
                billing_reason == "subscription_create"
                and (
                    subscription_status == "trialing"
                    or (
                        trial_end_dt is not None
                        and (line_start_dt is None or trial_end_dt.date() > line_start_dt.date())
                    )
                )
            )
            # Stripe can emit invoice.payment_succeeded when a trial starts (often amount=0).
            # Keep Subscribe for non-trial subscription starts and trial conversion billing.
            # Standard renewals also emit Subscribe, but should use only settled revenue, not projected LTV.
            should_subscribe = trial_conversion or (
                billing_reason == "subscription_create" and not trial_start_invoice
            )
            is_standard_renewal_subscribe = billing_reason == "subscription_cycle" and not trial_conversion
            if (should_subscribe or is_standard_renewal_subscribe) and owner_type == "user" and owner:
                marketing_properties = {
                    "plan": plan_value,
                    "subscription_id": subscription_id,
                    "stripe.invoice_id": payload.get("id"),
                }

                metadata = {}
                if subscription_data:
                    metadata = _coerce_metadata_dict(subscription_data.get("metadata"))
                if not metadata:
                    metadata = _coerce_metadata_dict(payload.get("metadata"))
                if is_standard_renewal_subscribe:
                    # Use invoice-scoped event_id for renewals so repeated webhook deliveries dedupe safely
                    # while avoiding reuse of the original checkout event_id stored on the subscription.
                    renewal_event_id = payload.get("id")
                    if renewal_event_id:
                        marketing_properties["event_id"] = str(renewal_event_id).strip()
                else:
                    event_id_override = metadata.get("gobii_event_id")
                    if isinstance(event_id_override, str) and event_id_override.strip():
                        marketing_properties["event_id"] = event_id_override.strip()

                value, currency = _calculate_subscription_value_from_lines(lines)
                if value is not None:
                    marketing_properties["transaction_value"] = value
                    marketing_properties["value"] = (
                        value
                        if is_standard_renewal_subscribe
                        else value * settings.CAPI_LTV_MULTIPLE
                    )
                if currency:
                    marketing_properties["currency"] = currency

                marketing_properties = {k: v for k, v in marketing_properties.items() if v is not None}

                if owner_type == "user":
                    subscribe_context = (
                        _build_off_session_marketing_context_from_user(owner)
                        if is_standard_renewal_subscribe
                        else _build_marketing_context_from_user(owner)
                    )
                else:
                    subscribe_context = {}
                checkout_source_url = metadata.get("checkout_source_url")
                # Recurring renewals happen off-session, so reusing the original
                # checkout URL would misattribute the conversion page.
                if checkout_source_url and not is_standard_renewal_subscribe:
                    subscribe_context["page"] = {"url": checkout_source_url}
                capi(
                    user=owner,
                    event_name="Subscribe",
                    properties=marketing_properties,
                    request=None,
                    context=subscribe_context,
                )
        except Exception:
            logger.exception(
                "Failed to enqueue marketing Subscribe event for invoice %s",
                payload.get("id"),
            )

@djstripe_receiver(["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"])
def handle_subscription_event(event, **kwargs):
    """Update user status and quota based on subscription events."""
    with tracer.start_as_current_span("handle_subscription_event") as span:
        payload = event.data.get("object", {})

        # 1. Ignore anything that isn't a subscription (defensive, though Stripe shouldn't send it)
        if payload.get("object") != "subscription":
            span.add_event('Ignoring non-subscription event')
            logger.warning("Unexpected Stripe object in webhook: %s", payload.get("object"))
            return

        status = stripe_status()
        if not status.enabled:
            span.add_event('Stripe disabled; ignoring webhook')
            logger.info("Stripe disabled; ignoring subscription webhook %s", payload.get("id"))
            return

        stripe_key = PaymentsHelper.get_stripe_key()
        if not stripe_key:
            span.add_event('Stripe key missing; ignoring webhook')
            logger.warning("Stripe key unavailable; ignoring subscription webhook %s", payload.get("id"))
            return

        stripe.api_key = stripe_key

        payload = _coerce_subscription_payload(payload)

        # Note: do not early-return on hard-deleted payloads; we still need to
        # downgrade the user to the free plan when a subscription is deleted.
        stripe_sub = None

        # 3. Normal create/update flow
        try:
            sub = Subscription.sync_from_stripe_data(payload)  # first try the cheap way
        except Exception as exc:
            logger.error("Failed to sync subscription data %s", exc)
            if "auto_paging_iter" in str(exc):
                # Fallback – pick ONE of the fixes above
                stripe_sub = stripe.Subscription.retrieve(  # or construct_from(...)
                    payload["id"],
                    expand=["items"],
                )

                sub = Subscription.sync_from_stripe_data(stripe_sub)
            else:
                logger.error("Failed to sync subscription data %s", exc)
                # TODO: Consider a more robust fallback or retry mechanism here if needed
                # For now, re-raising the exception might be acceptable if sync is critical
                raise

        sub, refreshed_subscription = _refresh_subscription_when_payload_mismatches_local(sub, payload)
        if refreshed_subscription is not None:
            stripe_sub = refreshed_subscription

        customer: Customer | None = sub.customer
        if not customer:
            span.add_event('Ignoring subscription with no customer')
            logger.info("Subscription %s has no linked customer; nothing to do.", sub.id)
            return

        span.set_attribute('subscription.customer.id', getattr(customer, 'id', ''))
        span.set_attribute('subscription.customer.email', getattr(customer, 'email', ''))

        owner = None
        owner_type = ""
        organization_billing: OrganizationBilling | None = None

        if customer.subscriber:
            owner = customer.subscriber
            owner_type = "user"
        else:
            organization_billing = (
                OrganizationBilling.objects.select_related("organization")
                .filter(stripe_customer_id=customer.id)
                .first()
            )
            if organization_billing and organization_billing.organization:
                owner = organization_billing.organization
                owner_type = "organization"

        if not owner:
            span.add_event('Ignoring subscription event with no owner')
            logger.info("Subscription %s has no linked billing owner; nothing to do.", sub.id)
            return

        span.set_attribute('subscription.owner.type', owner_type)

        subscription_id = getattr(sub, "id", None)
        marketing_context: dict[str, Any]
        plan_before_cancellation = None
        if owner_type == "user":
            marketing_context = _build_marketing_context_from_user(owner)

            try:
                plan_before_cancellation = owner.billing.subscription  # type: ignore[attr-defined]
            except UserBilling.DoesNotExist:
                plan_before_cancellation = None
            except AttributeError:
                plan_before_cancellation = None
        else:
            marketing_context = {}

        source_data = stripe_sub if stripe_sub is not None else (getattr(sub, "stripe_data", {}) or {})
        subscription_metadata: dict[str, Any] = {}
        if isinstance(source_data, Mapping):
            subscription_metadata = _coerce_metadata_dict(source_data.get("metadata"))
        if not subscription_metadata:
            subscription_metadata = _coerce_metadata_dict(getattr(sub, "metadata", None))
        mark_trial_promo_redemption_subscription(
            event_id=subscription_metadata.get("gobii_event_id"),
            stripe_subscription_id=subscription_id,
        )

        # Handle explicit deletions (downgrade to free immediately)
        try:
            event_type = getattr(event, "type", "") or getattr(event, "event_type", "")
        except Exception:
            event_type = ""

        span.set_attribute('subscription.event_type', event_type)
        previous_attributes = {}
        if isinstance(getattr(event, "data", None), Mapping):
            raw_previous_attributes = event.data.get("previous_attributes")
            if isinstance(raw_previous_attributes, Mapping):
                previous_attributes = raw_previous_attributes

        actor_user_id = _resolve_actor_user_id(owner, owner_type)
        owner_id_str = str(getattr(owner, "id", ""))
        stripe_event_id = str(getattr(event, "id", "")) or None

        current_subscription_status = _get_stripe_data_value(source_data, "status")
        if current_subscription_status is None:
            current_subscription_status = getattr(sub, "status", None)
        if isinstance(current_subscription_status, str):
            current_subscription_status = current_subscription_status.strip().lower()
        else:
            current_subscription_status = None

        current_cancel_at_period_end = _coerce_bool(_get_stripe_data_value(source_data, "cancel_at_period_end"))
        if current_cancel_at_period_end is None:
            current_cancel_at_period_end = _coerce_bool(getattr(sub, "cancel_at_period_end", None))

        classification_trial_end_dt = _coerce_datetime(_get_stripe_data_value(source_data, "trial_end"))
        if classification_trial_end_dt is None:
            classification_trial_end_dt = _coerce_datetime(getattr(sub, "trial_end", None))
        classification_current_period_end_dt = _coerce_datetime(
            _get_stripe_data_value(source_data, "current_period_end")
        )
        if classification_current_period_end_dt is None:
            classification_current_period_end_dt = _coerce_datetime(getattr(sub, "current_period_end", None))

        try:
            if is_trial_cancel_scheduled(
                event_type=event_type,
                current_status=current_subscription_status,
                current_cancel_at_period_end=current_cancel_at_period_end,
                previous_attributes=previous_attributes,
            ):
                emit_billing_lifecycle_event(
                    TRIAL_CANCEL_SCHEDULED,
                    sender=handle_subscription_event,
                    payload=BillingLifecyclePayload(
                        owner_type=owner_type,
                        owner_id=owner_id_str,
                        actor_user_id=actor_user_id,
                        subscription_id=str(subscription_id) if subscription_id else None,
                        stripe_event_id=stripe_event_id,
                        subscription_status=current_subscription_status,
                        occurred_at=timezone.now(),
                    ),
                )

            if is_subscription_delinquency_entered(
                event_type=event_type,
                current_status=current_subscription_status,
                previous_attributes=previous_attributes,
            ):
                emit_billing_lifecycle_event(
                    SUBSCRIPTION_DELINQUENCY_ENTERED,
                    sender=handle_subscription_event,
                    payload=BillingLifecyclePayload(
                        owner_type=owner_type,
                        owner_id=owner_id_str,
                        actor_user_id=actor_user_id,
                        subscription_id=str(subscription_id) if subscription_id else None,
                        stripe_event_id=stripe_event_id,
                        subscription_status=current_subscription_status,
                        occurred_at=timezone.now(),
                    ),
                )
        except Exception:
            # Intentionally broad: lifecycle emission is best-effort and must never
            # interrupt the core Stripe webhook path for subscription updates.
            logger.exception(
                "Failed to emit billing lifecycle transition event for subscription %s",
                subscription_id,
            )

        # Guardrail: when Stripe fires a new individual (non-org) subscription, ensure we reuse one subscription
        # per customer and cancel any older duplicates. This preserves add-ons (e.g., dedicated IPs/meters) on the newest sub.
        try:
            if event_type == "customer.subscription.created" and owner_type == "user":
                items_data = []
                if isinstance(source_data, Mapping):
                    items_data = ((source_data.get("items") or {}).get("data") or [])

                plan_products = _individual_plan_product_ids()
                plan_price_ids = _individual_plan_price_ids()
                licensed_price_id = None
                metered_price_id = None

                for item in items_data:
                    price = item.get("price") or {}
                    product = price.get("product")
                    if isinstance(product, Mapping):
                        product = product.get("id")

                    usage_type = price.get("usage_type") or (price.get("recurring") or {}).get("usage_type")

                    if not licensed_price_id and (
                        (product and product in plan_products)
                        or (price.get("id") and price.get("id") in plan_price_ids)
                    ):
                        licensed_price_id = price.get("id")
                    elif not metered_price_id and usage_type == "metered":
                        metered_price_id = price.get("id")

                customer_id = getattr(customer, "id", None)
                if licensed_price_id and customer_id:
                    ensure_single_individual_subscription(
                        customer_id=str(customer_id),
                        licensed_price_id=licensed_price_id,
                        metered_price_id=metered_price_id,
                        metadata=source_data.get("metadata") if isinstance(source_data, Mapping) else {},
                        idempotency_key=f"sub-webhook-upsert-{payload.get('id', '')}",
                        create_if_missing=False,
                    )
        except Exception:
            logger.warning(
                "Failed to ensure single individual subscription for customer %s during webhook",
                getattr(customer, "id", None),
                exc_info=True,
            )

        if event_type == "customer.subscription.deleted" or getattr(sub, "status", "") == "canceled":
            active_sub = get_active_subscription(owner)
            if active_sub and getattr(active_sub, "id", None) and getattr(active_sub, "id", None) != subscription_id:
                span.add_event(
                    "subscription.cancel_ignored_active_subscription",
                    {
                        "subscription.id": subscription_id or "",
                        "active_subscription.id": getattr(active_sub, "id", "") or "",
                    },
                )
                logger.info(
                    "Skipping downgrade for owner %s: subscription %s canceled but active subscription %s exists.",
                    getattr(owner, "id", None) or owner,
                    subscription_id,
                    getattr(active_sub, "id", None),
                )
                return

            ################################################################################
            #
            # Trial ended due to user choice (for example, they canceled the trial so it would
            # not convert)
            #
            ################################################################################
            try:
                if is_trial_ended_non_renewal(
                    event_type=event_type,
                    current_status=current_subscription_status,
                    previous_attributes=previous_attributes,
                    trial_end_dt=classification_trial_end_dt,
                    current_period_end_dt=classification_current_period_end_dt,
                    now_dt=timezone.now(),
                ):
                    emit_billing_lifecycle_event(
                        TRIAL_ENDED_NON_RENEWAL,
                        sender=handle_subscription_event,
                        payload=BillingLifecyclePayload(
                            owner_type=owner_type,
                            owner_id=owner_id_str,
                            actor_user_id=actor_user_id,
                            subscription_id=str(subscription_id) if subscription_id else None,
                            stripe_event_id=stripe_event_id,
                            subscription_status=current_subscription_status,
                            occurred_at=timezone.now(),
                        ),
                    )
            except Exception:
                # Intentionally broad: lifecycle emission is best-effort and must
                # never interrupt cancellation handling for Stripe subscriptions.
                logger.exception(
                    "Failed to emit trial ended lifecycle event for subscription %s",
                    subscription_id,
                )

            downgrade_owner_to_free_plan(owner)
            try:
                resume_owner_execution(
                    owner,
                    source=f"stripe.{event_type}.downgrade_to_free",
                )
            except Exception:
                logger.exception(
                    "Failed to resume owner execution after downgrade for owner %s",
                    getattr(owner, "id", None) or owner,
                )

            try:
                DedicatedProxyService.release_for_owner(owner)
            except Exception:
                logger.exception(
                    "Failed to release dedicated proxies for owner %s during cancellation",
                    getattr(owner, "id", None) or owner,
                )

            if owner_type == "user":
                try:
                    Analytics.identify(
                        owner.id,
                        {
                            'plan': PlanNames.FREE,
                            'is_trial': False,
                        },
                    )
                except Exception:
                    logger.exception("Failed to update user subscription in analytics for user %s", owner.id)

                try:
                    Analytics.track_event(
                        user_id=owner.id,
                        event=AnalyticsEvent.SUBSCRIPTION_CANCELLED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'plan': PlanNames.FREE,
                            'stripe.subscription_id': getattr(sub, 'id', None),
                        },
                    )
                except Exception:
                    logger.exception("Failed to track subscription cancellation for user %s", owner.id)

                try:
                    cancel_plan_value = _extract_plan_value_from_subscription(source_data) or plan_before_cancellation
                    cancel_properties = {
                        "plan": cancel_plan_value or PlanNames.FREE,
                        "subscription_id": subscription_id,
                        "status": "canceled",
                        "churn_stage": "voluntary",
                    }
                    cancel_properties = {k: v for k, v in cancel_properties.items() if v is not None}
                    cancel_context = dict(marketing_context)
                    cancel_context["page"] = {"url": f"{settings.PUBLIC_SITE_URL.rstrip('/')}/console/billing/"}
                    capi(
                        user=owner,
                        event_name="CancelSubscription",
                        properties=cancel_properties,
                        request=None,
                        context=cancel_context,
                    )
                except Exception:
                    logger.exception(
                        "Failed to enqueue marketing cancellation event for user %s",
                        getattr(owner, "id", None),
                    )
            else:
                billing = organization_billing
                if billing:
                    updates: list[str] = []
                    if billing.purchased_seats != 0:
                        billing.purchased_seats = 0
                        updates.append("purchased_seats")
                    if getattr(billing, "stripe_subscription_id", None):
                        billing.stripe_subscription_id = None
                        updates.append("stripe_subscription_id")
                    if getattr(billing, "cancel_at", None):
                        billing.cancel_at = None
                        updates.append("cancel_at")
                    if getattr(billing, "cancel_at_period_end", False):
                        billing.cancel_at_period_end = False
                        updates.append("cancel_at_period_end")
                    if updates:
                        billing.save(update_fields=updates)
            return

        # Prefer explicit Stripe retrieve when present; otherwise use dj-stripe's cached payload
        # from the Subscription row. This allows the normal sync_from_stripe_data path to work.

        current_period_start_dt = _coerce_datetime(_get_stripe_data_value(source_data, "current_period_start"))
        current_period_end_dt = _coerce_datetime(_get_stripe_data_value(source_data, "current_period_end"))
        trial_start_dt = _coerce_datetime(_get_stripe_data_value(source_data, "trial_start"))
        trial_end_dt = _coerce_datetime(_get_stripe_data_value(source_data, "trial_end"))
        cancel_at_dt = _coerce_datetime(_get_stripe_data_value(source_data, "cancel_at"))
        cancel_at_period_end_flag = _coerce_bool(_get_stripe_data_value(source_data, "cancel_at_period_end"))

        span.set_attribute('subscription.current_period_start', str(current_period_start_dt))
        span.set_attribute('subscription.current_period_end', str(current_period_end_dt))
        span.set_attribute('subscription.trial_start', str(trial_start_dt))
        span.set_attribute('subscription.trial_end', str(trial_end_dt))
        span.set_attribute('subscription.cancel_at', str(cancel_at_dt))
        span.set_attribute('subscription.cancel_at_period_end', str(cancel_at_period_end_flag))

        if current_period_end_dt is None:
            current_period_end_dt = _coerce_datetime(getattr(sub, "current_period_end", None))
            span.set_attribute('subscription.current_period_end_fallback', str(current_period_end_dt))

        if cancel_at_dt is None:
            cancel_at_dt = _coerce_datetime(getattr(sub, "cancel_at", None))
            span.set_attribute('subscription.cancel_at_fallback', str(cancel_at_dt))
        if cancel_at_period_end_flag is None:
            cancel_at_period_end_flag = _coerce_bool(getattr(sub, "cancel_at_period_end", None))
            span.set_attribute('subscription.cancel_at_period_end_fallback', str(cancel_at_period_end_flag))

        invoice_id = _get_stripe_data_value(source_data, "latest_invoice") or getattr(sub, "latest_invoice", None)
        span.set_attribute('subscription.invoice_id', str(invoice_id))

        billing_reason = _get_stripe_data_value(source_data, "billing_reason")
        if billing_reason is None:
            billing_reason = getattr(sub, "billing_reason", None)

        if invoice_id and not billing_reason:
            try:
                invoice_data = stripe.Invoice.retrieve(invoice_id)
                invoice = Invoice.sync_from_stripe_data(invoice_data)
                billing_reason = getattr(invoice, "billing_reason", None)
                if billing_reason is None:
                    billing_reason = _get_stripe_data_value(getattr(invoice, "stripe_data", {}) or {}, "billing_reason")
            except Exception as exc:
                span.add_event('invoice.fetch_failed', {'invoice.id': invoice_id})
                logger.warning(
                    "Webhook: failed to fetch invoice %s for subscription %s: %s",
                    invoice_id,
                    getattr(sub, 'id', ''),
                    exc,
                )

        plan = None
        plan_version = None
        licensed_item = None
        try:
            plan, plan_version, licensed_item = resolve_plan_from_subscription_data(
                source_data if isinstance(source_data, Mapping) else None,
                owner_type=owner_type,
            )
        except Exception as e:
            logger.warning("Webhook: failed to inspect subscription items for %s: %s", sub.id, e)

        # Proceed when the subscription is active or trialing and we found a licensed item
        span.set_attribute('subscription.status', str(sub.status))
        if sub.status in ("active", "trialing") and licensed_item is not None:
            try:
                resume_owner_execution(owner, source=f"stripe.{event_type}")
            except Exception:
                logger.exception(
                    "Failed to resume owner execution for active subscription owner %s",
                    getattr(owner, "id", None) or owner,
                )

            price_info = licensed_item.get("price") or {}
            if not isinstance(price_info, Mapping):
                price_info = {}

            price_id = price_info.get("id") or price_info.get("price")
            product_id = price_info.get("product")
            if isinstance(product_id, Mapping):
                product_id = product_id.get("id")

            if not plan and product_id:
                plan = get_plan_by_product_id(product_id)

            invoice_id = source_data.get("latest_invoice")

            plan_value = None
            if plan_version:
                plan_value = plan_version.legacy_plan_code or plan_version.plan.slug
            if not plan_value and plan:
                plan_value = plan.get("id")
            if not plan_value:
                plan_value = PlanNames.FREE
            if not plan:
                plan = PLAN_CONFIG.get(PlanNames.FREE)

            items_data: list[Mapping[str, Any]] = []
            try:
                items_data = ((source_data.get("items") or {}).get("data") or []) if isinstance(source_data, Mapping) else []
            except Exception:
                items_data = []

            try:
                AddonEntitlementService.sync_subscription_entitlements(
                    owner=owner,
                    owner_type=owner_type,
                    plan_id=plan_value,
                    plan_version=plan_version,
                    subscription_items=items_data,
                    period_start=current_period_start_dt or timezone.now(),
                    period_end=current_period_end_dt,
                    created_via="subscription_webhook",
                )
            except Exception:
                logger.exception(
                    "Failed to sync add-on entitlements for owner %s during subscription webhook",
                    getattr(owner, "id", None) or owner,
                )

            stripe_settings = get_stripe_settings()

            if owner_type == "user":
                prior_plan_value = plan_before_cancellation
                mark_user_billing_with_plan(owner, plan_value, update_anchor=False, plan_version=plan_version)
                try:
                    resume_signup_preview_agents_for_user_if_eligible(
                        owner,
                        reconcile_plan=False,
                        resume_source="webhook",
                        plan_before=prior_plan_value,
                        plan_after=plan_value,
                    )
                except Exception:
                    logger.exception(
                        "Failed to resume signup preview agents for user %s after subscription update",
                        getattr(owner, "id", None),
                    )
                plan_changed_for_user = (
                    event_type == "customer.subscription.updated"
                    and bool(prior_plan_value)
                    and prior_plan_value != plan_value
                )
                should_grant = billing_reason in {"subscription_create", "subscription_cycle"}
                if billing_reason is None and event_type == "customer.subscription.created":
                    should_grant = True
                if plan_changed_for_user:
                    should_grant = True
                trial_conversion = False
                if sub.status == "active" and trial_end_dt and current_period_start_dt:
                    trial_conversion = trial_end_dt.date() == current_period_start_dt.date()
                if trial_conversion:
                    should_grant = True
                if should_grant:
                    credit_override = None
                    grant_invoice_id = invoice_id or ""
                    expiration_override = None
                    free_trial_start_grant = False
                    if sub.status == "trialing":
                        expiration_override = _trial_paid_period_end(trial_end_dt, current_period_end_dt)
                        free_trial_start_grant = bool(
                            billing_reason == "subscription_create"
                            or (billing_reason is None and event_type == "customer.subscription.created")
                        )
                        monthly_credits = None
                        if isinstance(plan, Mapping):
                            monthly_credits = plan.get("monthly_task_credits")
                        try:
                            monthly_credits = int(monthly_credits) if monthly_credits is not None else None
                        except (TypeError, ValueError):
                            monthly_credits = None
                        if monthly_credits is None:
                            should_grant = False
                        else:
                            trial_credit_amount = parse_trial_promo_credit_amount(subscription_metadata)
                            if trial_credit_amount is None:
                                trial_credit_amount = _trial_start_credit_amount(
                                    plan_id=plan_value,
                                    monthly_credits=monthly_credits,
                                )
                            if trial_credit_amount != Decimal(monthly_credits):
                                credit_override = trial_credit_amount
                        if not grant_invoice_id:
                            anchor_dt = trial_start_dt or current_period_start_dt
                            anchor = anchor_dt.date().isoformat() if anchor_dt else "start"
                            grant_invoice_id = f"trial:{subscription_id}:{anchor}"
                    elif trial_conversion:
                        monthly_credits = None
                        if isinstance(plan, Mapping):
                            monthly_credits = plan.get("monthly_task_credits")
                        try:
                            monthly_credits = int(monthly_credits) if monthly_credits is not None else None
                        except (TypeError, ValueError):
                            monthly_credits = None

                        if monthly_credits is None:
                            should_grant = False
                        else:
                            topoff = _trial_topoff_amount(
                                owner=owner,
                                plan_id=plan_value,
                                monthly_credits=monthly_credits,
                                as_of=current_period_start_dt or timezone.now(),
                            )
                            if topoff <= 0:
                                should_grant = False
                            else:
                                credit_override = topoff
                                if not grant_invoice_id:
                                    anchor = (
                                        current_period_start_dt.date().isoformat()
                                        if current_period_start_dt
                                        else "start"
                                    )
                                    grant_invoice_id = f"trial-topoff:{subscription_id}:{anchor}"
                                expiration_override = current_period_end_dt
                    elif plan_changed_for_user:
                        monthly_credits = None
                        if isinstance(plan, Mapping):
                            try:
                                monthly_credits = int(plan.get("monthly_task_credits"))
                            except (TypeError, ValueError):
                                pass

                        if monthly_credits is None:
                            should_grant = False
                        else:
                            topoff = _owner_plan_topoff_amount(
                                owner=owner,
                                monthly_credits=monthly_credits,
                                as_of=timezone.now(),
                            )
                            if topoff <= 0:
                                should_grant = False
                            else:
                                credit_override = topoff
                                if not grant_invoice_id:
                                    anchor = (
                                        current_period_start_dt.date().isoformat()
                                        if current_period_start_dt
                                        else "start"
                                    )
                                    grant_invoice_id = f"plan-topoff:{subscription_id}:{anchor}:{plan_value}"
                                expiration_override = current_period_end_dt

                    if not should_grant:
                        credit_override = None

                    if should_grant:
                        TaskCreditService.grant_subscription_credits(
                            owner,
                            plan=plan,
                            invoice_id=grant_invoice_id,
                            credit_override=credit_override,
                            expiration_date=expiration_override,
                            free_trial_start=free_trial_start_grant,
                        )

                try:
                    ub = owner.billing
                    if current_period_start_dt:
                        new_day = current_period_start_dt.day
                        if ub.billing_cycle_anchor != new_day:
                            ub.billing_cycle_anchor = new_day
                            ub.save(update_fields=["billing_cycle_anchor"])
                except UserBilling.DoesNotExist as ue:
                    logger.exception("UserBilling record not found for user %s during anchor alignment: %s", owner.id, ue)
                except Exception as e:
                    logger.exception("Failed to align billing anchor with Stripe period for user %s: %s", owner.id, e)

                try:
                    Analytics.identify(owner.id, {
                        'plan': plan_value,
                        'is_trial': sub.status == "trialing",
                    })
                except Exception:
                    logger.exception(
                        "Failed to identify subscription analytics state for user %s during webhook",
                        getattr(owner, "id", None),
                    )

                event_properties = {
                    'plan': plan_value,
                }
                if invoice_id:
                    event_properties['stripe.invoice_id'] = invoice_id

                analytics_event = None
                if billing_reason == 'subscription_create':
                    analytics_event = AnalyticsEvent.SUBSCRIPTION_CREATED
                elif billing_reason == 'subscription_cycle':
                    analytics_event = AnalyticsEvent.SUBSCRIPTION_RENEWED

                suppress_marketing_event = False
                if (
                    analytics_event == AnalyticsEvent.SUBSCRIPTION_CREATED
                    and event_type != "customer.subscription.created"
                ):
                    suppress_marketing_event = True

                if analytics_event:
                    Analytics.track_event(
                        user_id=owner.id,
                        event=analytics_event,
                        source=AnalyticsSource.WEB,
                        properties=event_properties,
                    )
                    if (
                        analytics_event == AnalyticsEvent.SUBSCRIPTION_CREATED
                        and sub.status == "trialing"
                        and event_type == "customer.subscription.created"
                    ):
                        trial_properties = {
                            "plan": plan_value,
                            "subscription_id": subscription_id,
                        }
                        if invoice_id:
                            trial_properties["stripe.invoice_id"] = invoice_id
                        Analytics.track_event(
                            user_id=owner.id,
                            event=AnalyticsEvent.BILLING_TRIAL_STARTED,
                            source=AnalyticsSource.WEB,
                            properties=trial_properties,
                        )
                    marketing_properties = {
                        "plan": plan_value,
                        "subscription_id": subscription_id,
                    }
                    if analytics_event == AnalyticsEvent.SUBSCRIPTION_CREATED:
                        event_id_override = subscription_metadata.get("gobii_event_id")
                        if isinstance(event_id_override, str) and event_id_override.strip():
                            marketing_properties["event_id"] = event_id_override.strip()

                    if sub.status == "trialing":
                        value, currency = _calculate_subscription_value(licensed_item)
                        conversion_rate = resolve_start_trial_conversion_rate(
                            plan_value,
                            default_rate=settings.CAPI_START_TRIAL_CONV_RATE,
                            scale_rate=settings.CAPI_START_TRIAL_SCALE_CONV_RATE,
                        )
                        predicted_ltv, conversion_value = calculate_start_trial_values(
                            value,
                            ltv_multiple=settings.CAPI_LTV_MULTIPLE,
                            conversion_rate=conversion_rate,
                        )
                        if predicted_ltv is not None:
                            marketing_properties["predicted_ltv"] = predicted_ltv
                        if conversion_value is not None:
                            marketing_properties["value"] = conversion_value
                        marketing_properties["currency"] = "USD"

                    marketing_properties = {k: v for k, v in marketing_properties.items() if v is not None}

                    if not suppress_marketing_event:
                        try:
                            if analytics_event != AnalyticsEvent.SUBSCRIPTION_RENEWED and sub.status == "trialing":
                                checkout_source_url = subscription_metadata.get("checkout_source_url")
                                if checkout_source_url:
                                    marketing_context["page"] = {"url": checkout_source_url}
                                capi(
                                    user=owner,
                                    event_name="StartTrial",
                                    properties=marketing_properties,
                                    request=None,
                                    context=marketing_context,
                                )
                            # Subscribe event is sent on first payment (invoice.payment_succeeded).
                        except Exception:
                            logger.exception(
                                "Failed to enqueue marketing subscription event for user %s",
                                getattr(owner, "id", None),
                            )
            else:
                seats = 0
                try:
                    seats = int(licensed_item.get("quantity") or 0)
                except (TypeError, ValueError):
                    seats = 0

                prev_seats = 0
                if organization_billing:
                    prev_seats = getattr(organization_billing, "purchased_seats", 0)

                overage_price_id = stripe_settings.org_team_additional_task_price_id
                if overage_price_id:
                    items_data = source_data.get("items", {}).get("data", []) or []
                    has_overage_item = any(
                        (item.get("price") or {}).get("id") == overage_price_id
                        for item in items_data
                    )

                    metadata: dict[str, str] = dict(subscription_metadata)
                    overage_state = metadata.get(ORG_OVERAGE_STATE_META_KEY, "")
                    seat_delta = seats - prev_seats

                    should_reattach = not has_overage_item and (
                        overage_state != ORG_OVERAGE_STATE_DETACHED_PENDING or seat_delta != 0
                    )

                    if should_reattach:
                        subscription_id = getattr(sub, "id", "")
                        already_present = False
                        try:
                            live_subscription = stripe.Subscription.retrieve(
                                subscription_id,
                                expand=["items.data.price"],
                            )
                            live_items = (live_subscription.get("items") or {}).get("data", []) if isinstance(live_subscription, Mapping) else []
                            already_present = any(
                                (item.get("price") or {}).get("id") == overage_price_id
                                for item in live_items or []
                            )
                        except Exception as exc:  # pragma: no cover - unexpected Stripe error
                            logger.warning(
                                "Failed to refresh subscription %s before reattaching overage SKU: %s",
                                subscription_id,
                                exc,
                            )

                        if not already_present:
                            try:
                                stripe.SubscriptionItem.create(
                                    subscription=subscription_id,
                                    price=overage_price_id,
                                )
                                span.add_event(
                                    "org_subscription_overage_item_added",
                                    {
                                        "subscription.id": subscription_id,
                                        "price.id": overage_price_id,
                                    },
                                )
                            except stripe.error.InvalidRequestError as exc:
                                logger.warning(
                                    "Overage price %s already present on subscription %s when reattaching: %s",
                                    overage_price_id,
                                    subscription_id,
                                    exc,
                                )
                                already_present = True
                            except Exception as exc:  # pragma: no cover - unexpected Stripe error
                                logger.exception(
                                    "Failed to attach org overage price %s to subscription %s: %s",
                                    overage_price_id,
                                    subscription_id,
                                    exc,
                                )
                        else:
                            span.add_event(
                                "org_subscription_overage_item_exists",
                                {
                                    "subscription.id": subscription_id,
                                    "price.id": overage_price_id,
                                },
                            )

                        if (overage_state == ORG_OVERAGE_STATE_DETACHED_PENDING) and (already_present or not should_reattach):
                            try:
                                stripe.Subscription.modify(
                                    subscription_id,
                                    metadata={ORG_OVERAGE_STATE_META_KEY: ""},
                                )
                            except Exception as exc:  # pragma: no cover - unexpected Stripe error
                                logger.warning(
                                    "Failed to clear overage detach flag on subscription %s: %s",
                                    subscription_id,
                                    exc,
                                )
                    elif has_overage_item and overage_state == ORG_OVERAGE_STATE_DETACHED_PENDING:
                        try:
                            stripe.Subscription.modify(
                                getattr(sub, "id", ""),
                                metadata={ORG_OVERAGE_STATE_META_KEY: ""},
                            )
                        except Exception as exc:  # pragma: no cover - unexpected Stripe error
                            logger.warning(
                                "Failed to clear overage detach flag on subscription %s: %s",
                                getattr(sub, "id", ""),
                                exc,
                            )

                billing = mark_owner_billing_with_plan(
                    owner,
                    plan_value,
                    update_anchor=False,
                    plan_version=plan_version,
                )
                if billing:
                    updates: list[str] = []
                    if current_period_start_dt:
                        new_day = current_period_start_dt.day
                        if billing.billing_cycle_anchor != new_day:
                            billing.billing_cycle_anchor = new_day
                            updates.append("billing_cycle_anchor")

                    new_subscription_id = getattr(sub, 'id', None)
                    if getattr(billing, 'stripe_subscription_id', None) != new_subscription_id:
                        billing.stripe_subscription_id = new_subscription_id
                        updates.append("stripe_subscription_id")

                    if seats and getattr(billing, 'purchased_seats', None) != seats:
                        billing.purchased_seats = seats
                        updates.append("purchased_seats")

                    pending_schedule_id = getattr(billing, "pending_seat_schedule_id", "")
                    if pending_schedule_id and seats != prev_seats:
                        billing.pending_seat_quantity = None
                        billing.pending_seat_effective_at = None
                        billing.pending_seat_schedule_id = ""
                        for field in (
                            "pending_seat_quantity",
                            "pending_seat_effective_at",
                            "pending_seat_schedule_id",
                        ):
                            if field not in updates:
                                updates.append(field)

                    if hasattr(billing, 'cancel_at'):
                        if billing.cancel_at != cancel_at_dt:
                            billing.cancel_at = cancel_at_dt
                            updates.append("cancel_at")

                    if hasattr(billing, 'cancel_at_period_end'):
                        if cancel_at_period_end_flag is not None and billing.cancel_at_period_end != cancel_at_period_end_flag:
                            billing.cancel_at_period_end = cancel_at_period_end_flag
                            updates.append("cancel_at_period_end")

                    if updates:
                        billing.save(update_fields=updates)

                if seats > 0:
                    seats_to_grant = 0
                    if billing_reason in {"subscription_create", "subscription_cycle"}:
                        if billing_reason == "subscription_create" and prev_seats > 0:
                            seats_to_grant = max(seats - prev_seats, 0)
                        else:
                            seats_to_grant = seats
                    elif billing_reason == "subscription_update" and seats > prev_seats:
                        seats_to_grant = seats - prev_seats

                    if seats_to_grant > 0:
                        grant_invoice_id = ""
                        if invoice_id and (
                            billing_reason == "subscription_cycle"
                            or (billing_reason == "subscription_create" and prev_seats == 0)
                        ):
                            grant_invoice_id = invoice_id

                        # For cycle starts we want to reset the active monthly block
                        # instead of stacking an extra TaskCredit record.
                        replace_current = source_data.get("billing_reason") in {"subscription_create", "subscription_cycle"}

                        TaskCreditService.grant_subscription_credits_for_organization(
                            owner,
                            seats=seats_to_grant,
                            plan=plan,
                            invoice_id=grant_invoice_id,
                            subscription=sub,
                            replace_current=replace_current,
                        )

            _sync_dedicated_ip_allocations(owner, owner_type, source_data, stripe_settings)
