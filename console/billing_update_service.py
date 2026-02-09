import json
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Mapping

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from billing.addons import AddonEntitlementService
from billing.services import BillingService
from config.stripe_config import get_stripe_settings
from constants.plans import PlanNamesChoices
from console.context_helpers import build_console_context
from console.role_constants import BILLING_MANAGE_ROLES
from util.integrations import stripe_status
from util.payments_helper import PaymentsHelper
from util.subscription_helper import (
    ensure_single_individual_subscription,
    get_active_subscription,
    get_or_create_stripe_customer,
    get_organization_plan,
    get_user_plan,
)

from api.models import BrowserUseAgent
from api.services.dedicated_proxy_service import (
    DedicatedProxyService,
    DedicatedProxyUnavailableError,
)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore

logger = logging.getLogger(__name__)

SUPPORT_DETAIL = (
    "An error occurred while updating billing. "
    "Please contact support@gobii.ai for help."
)


class BillingUpdateError(Exception):
    def __init__(
        self,
        code: str,
        *,
        status: int = 400,
        detail: str | None = None,
        extra: dict[str, object] | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail
        self.extra = extra or {}


def _error_payload(exc: BillingUpdateError) -> tuple[dict[str, object], int]:
    payload: dict[str, object] = {"ok": False, "error": exc.code}
    if exc.detail:
        payload["detail"] = exc.detail
    payload.update(exc.extra)
    return payload, exc.status


def _assign_stripe_api_key() -> str:
    """Ensure Stripe secret key is configured before making API calls."""
    if stripe is None:
        raise ImproperlyConfigured("Stripe SDK is not installed while billing is enabled.")
    key = PaymentsHelper.get_stripe_key()
    if not key:
        raise ImproperlyConfigured("Stripe secret key missing while billing is enabled.")
    stripe.api_key = key
    return key


def _get_owner_plan_id(owner, owner_type: str) -> str | None:
    if owner_type == "organization":
        plan = get_organization_plan(owner)
    else:
        plan = get_user_plan(owner)
    return (plan or {}).get("id")


def _stripe_action_url_from_latest_invoice(subscription_data: Mapping[str, Any] | None) -> str | None:
    if not isinstance(subscription_data, Mapping):
        return None
    invoice = subscription_data.get("latest_invoice")
    if not isinstance(invoice, Mapping):
        return None
    hosted_url = invoice.get("hosted_invoice_url")
    payment_intent = invoice.get("payment_intent")
    if not hosted_url or not isinstance(payment_intent, Mapping):
        return None
    intent_status = payment_intent.get("status")
    if intent_status in {"requires_action", "requires_payment_method"}:
        return str(hosted_url)
    return None


def apply_addon_price_quantities(
    owner,
    owner_type: str,
    *,
    desired_quantities: Mapping[str, int],
    created_via: str,
    end_trial_on_purchase: bool,
) -> str | None:
    """Update add-on price quantities on Stripe and sync entitlements locally.

    Returns a hosted invoice URL when customer action is required (SCA / payment method).
    """
    if not isinstance(desired_quantities, Mapping) or not desired_quantities:
        raise BillingUpdateError("invalid_addon_quantities", status=400)

    plan_id = _get_owner_plan_id(owner, owner_type)
    task_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "task_pack")
    contact_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "contact_pack")
    browser_task_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "browser_task_limit")
    advanced_captcha_options = AddonEntitlementService.get_price_options(
        owner_type,
        plan_id,
        "advanced_captcha_resolution",
    )
    all_options = (task_options or []) + (contact_options or []) + (browser_task_options or []) + (advanced_captcha_options or [])
    allowed_price_ids = {opt.price_id for opt in all_options if getattr(opt, "price_id", None)}
    if not allowed_price_ids:
        raise BillingUpdateError("addons_not_configured", status=400)

    normalized: dict[str, int] = {}
    for price_id, raw_qty in desired_quantities.items():
        pid = str(price_id).strip()
        if not pid:
            continue
        if pid not in allowed_price_ids:
            raise BillingUpdateError("invalid_addon_price", status=400)
        try:
            qty = int(raw_qty)
        except (TypeError, ValueError):
            raise BillingUpdateError("invalid_addon_quantities", status=400)
        if qty < 0 or qty > 999:
            raise BillingUpdateError("invalid_addon_quantities", status=400)
        normalized[pid] = qty

    if not normalized:
        raise BillingUpdateError("invalid_addon_quantities", status=400)

    subscription = get_active_subscription(owner, preferred_plan_id=plan_id)
    if not subscription:
        # This is unexpected from the console UI perspective (billing state desync),
        # so surface the generic support message rather than a raw error code.
        logger.warning(
            "Billing update requested but no active subscription found for %s %s (plan=%s)",
            owner_type,
            getattr(owner, "id", None),
            plan_id,
        )
        raise BillingUpdateError("no_active_subscription", status=400, detail=SUPPORT_DETAIL)

    try:
        _assign_stripe_api_key()
        stripe_subscription = stripe.Subscription.retrieve(
            subscription.id,
            expand=["customer", "items.data.price", "latest_invoice.payment_intent", "latest_invoice"],
        )
        items_data = (stripe_subscription.get("items") or {}).get("data", []) if isinstance(stripe_subscription, Mapping) else []

        existing_qty: dict[str, int] = {}
        item_id_by_price: dict[str, str] = {}
        for item in items_data or []:
            price = item.get("price") or {}
            pid = price.get("id")
            if not pid:
                continue
            item_id_by_price[pid] = item.get("id")
            try:
                existing_qty[pid] = int(item.get("quantity") or 0)
            except (TypeError, ValueError):
                existing_qty[pid] = 0

        is_trialing = (stripe_subscription.get("status") or "") == "trialing"
        is_purchase = False
        items_payload: list[dict[str, Any]] = []
        for price_id, desired_qty in normalized.items():
            current_qty = existing_qty.get(price_id, 0)
            if desired_qty == current_qty:
                continue
            if desired_qty > current_qty:
                is_purchase = True
            if desired_qty > 0:
                if price_id in item_id_by_price:
                    items_payload.append({"id": item_id_by_price[price_id], "quantity": desired_qty})
                else:
                    items_payload.append({"price": price_id, "quantity": desired_qty})
            else:
                if price_id in item_id_by_price:
                    items_payload.append({"id": item_id_by_price[price_id], "deleted": True})

        updated_items = items_data
        stripe_action_url = None
        updated_subscription = None
        if items_payload:
            modify_kwargs: dict[str, object] = {
                "items": items_payload,
                "proration_behavior": "always_invoice",
                "expand": ["items.data.price", "latest_invoice.payment_intent", "latest_invoice"],
            }
            if is_purchase or not any(item.get("deleted") for item in items_payload):
                modify_kwargs["payment_behavior"] = "pending_if_incomplete"
            if end_trial_on_purchase and is_trialing and is_purchase:
                modify_kwargs["trial_end"] = "now"
            updated_subscription = stripe.Subscription.modify(subscription.id, **modify_kwargs)
            updated_items = (updated_subscription.get("items") or {}).get("data", []) if isinstance(updated_subscription, Mapping) else []
            stripe_action_url = _stripe_action_url_from_latest_invoice(updated_subscription)

        period_start_dt = None
        period_end_dt = None
        if isinstance(updated_subscription, Mapping):
            start_ts = updated_subscription.get("current_period_start")
            end_ts = updated_subscription.get("current_period_end")
            if start_ts and end_ts:
                try:
                    period_start_dt = datetime.fromtimestamp(int(start_ts), tz=dt_timezone.utc)
                    period_end_dt = datetime.fromtimestamp(int(end_ts), tz=dt_timezone.utc)
                except (TypeError, ValueError, OSError):
                    period_start_dt = None
                    period_end_dt = None

        if period_start_dt is None or period_end_dt is None:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)
            tz = timezone.get_current_timezone()
            period_start_dt = timezone.make_aware(datetime.combine(period_start, datetime.min.time()), tz)
            period_end_dt = timezone.make_aware(
                datetime.combine(period_end + timedelta(days=1), datetime.min.time()),
                tz,
            )

        with transaction.atomic():
            AddonEntitlementService.sync_subscription_entitlements(
                owner=owner,
                owner_type=owner_type,
                plan_id=plan_id,
                subscription_items=updated_items,
                period_start=period_start_dt,
                period_end=period_end_dt,
                created_via=created_via,
            )

        return stripe_action_url
    except stripe.error.StripeError as exc:
        logger.warning(
            "Stripe error updating add-ons for %s %s: %s",
            owner_type,
            getattr(owner, "id", None),
            exc,
        )
        raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)


def apply_dedicated_ip_changes(
    owner,
    owner_type: str,
    *,
    add_quantity: int,
    remove_proxy_ids: list[str],
    unassign_proxy_ids: set[str],
) -> str | None:
    """Apply dedicated IP changes in the DB, then reconcile Stripe to the actual quantity.

    Returns a hosted invoice URL when customer action is required (SCA / payment method).
    """
    plan_id = _get_owner_plan_id(owner, owner_type)
    if plan_id in (PlanNamesChoices.FREE.value, PlanNamesChoices.FREE):
        raise BillingUpdateError("plan_required", status=400)

    if add_quantity < 0 or add_quantity > 99:
        raise BillingUpdateError("invalid_dedicated_ips", status=400)

    normalized_remove: list[str] = []
    for proxy_id in remove_proxy_ids or []:
        pid = str(proxy_id).strip()
        if pid and pid not in normalized_remove:
            normalized_remove.append(pid)

    if normalized_remove:
        # Dedicated IP removal should be safe by default: if an IP is being removed,
        # automatically clear any agent assignments for this owner.
        unassign_proxy_ids = set(unassign_proxy_ids or set()).union(set(normalized_remove))

        owned_ids = {
            str(pid)
            for pid in (
                DedicatedProxyService.allocated_proxies(owner)
                .filter(id__in=normalized_remove)
                .values_list("id", flat=True)
            )
        }
        for proxy_id in normalized_remove:
            if proxy_id not in owned_ids:
                raise BillingUpdateError(
                    "dedicated_ip_not_owned",
                    status=400,
                    extra={"proxyId": proxy_id},
                )

        with transaction.atomic():
            if unassign_proxy_ids:
                browser_qs = BrowserUseAgent.objects.filter(preferred_proxy_id__in=list(unassign_proxy_ids))
                if owner_type == "organization":
                    browser_qs = browser_qs.filter(persistent_agent__organization=owner)
                else:
                    browser_qs = browser_qs.filter(
                        persistent_agent__user=owner,
                        persistent_agent__organization__isnull=True,
                    )
                browser_qs.update(preferred_proxy=None)

            for proxy_id in normalized_remove:
                released = DedicatedProxyService.release_specific(owner, proxy_id)
                if not released:
                    raise BillingUpdateError(
                        "dedicated_ip_already_released",
                        status=400,
                        extra={"proxyId": proxy_id},
                    )

    allocated_proxy_ids: list[str] = []
    if add_quantity:
        for _ in range(add_quantity):
            try:
                proxy = DedicatedProxyService.allocate_proxy(owner)
                allocated_proxy_ids.append(str(proxy.id))
            except DedicatedProxyUnavailableError:
                break

    try:
        actual_qty = DedicatedProxyService.allocated_count(owner)
        action_url = _update_stripe_dedicated_ip_quantity(
            owner,
            owner_type,
            actual_qty,
            end_trial_now=bool(allocated_proxy_ids),
        )
        return action_url
    except stripe.error.StripeError as exc:
        # If we allocated inventory but couldn't bill for it, roll it back.
        if allocated_proxy_ids:
            with transaction.atomic():
                for proxy_id in allocated_proxy_ids:
                    try:
                        DedicatedProxyService.release_specific(owner, proxy_id)
                    except Exception:
                        logger.exception("Failed to rollback allocated dedicated proxy %s for %s", proxy_id, getattr(owner, "id", None))
        logger.warning(
            "Stripe error updating dedicated IP quantity for %s %s: %s",
            owner_type,
            getattr(owner, "id", None),
            exc,
        )
        raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)
    except (ValueError, ImproperlyConfigured) as exc:
        raise BillingUpdateError("invalid_dedicated_ips", status=400, detail=str(exc))


def _update_stripe_dedicated_ip_quantity(
    owner,
    owner_type: str,
    desired_qty: int,
    *,
    end_trial_now: bool = False,
) -> str | None:
    """Mirror dedicated IP quantity on Stripe. Returns hosted invoice URL when action is required."""
    desired_qty = int(desired_qty)
    subscription = get_active_subscription(owner)
    if not subscription:
        raise ValueError("Active subscription not found")

    stripe_settings = get_stripe_settings()
    dedicated_price_id = (
        stripe_settings.startup_dedicated_ip_price_id
        if owner_type == "user"
        else stripe_settings.org_team_dedicated_ip_price_id
    )
    if not dedicated_price_id:
        raise ValueError("Dedicated IP price not configured")

    _assign_stripe_api_key()
    subscription_data = stripe.Subscription.retrieve(
        subscription.id,
        expand=["items.data.price", "latest_invoice.payment_intent", "latest_invoice"],
    )

    dedicated_item = None
    for item in subscription_data.get("items", {}).get("data", []) or []:
        price = item.get("price") or {}
        if price.get("id") == dedicated_price_id:
            dedicated_item = item
            break

    if desired_qty > 0:
        if dedicated_item is None:
            items_payload = [{"price": dedicated_price_id, "quantity": desired_qty}]
        else:
            items_payload = [{"id": dedicated_item.get("id"), "quantity": desired_qty}]
        modify_kwargs = {
            "items": items_payload,
            "proration_behavior": "always_invoice",
            "expand": ["items.data.price", "latest_invoice.payment_intent", "latest_invoice"],
            "payment_behavior": "pending_if_incomplete",
        }
        if end_trial_now and (subscription_data.get("status") or "") == "trialing":
            modify_kwargs["trial_end"] = "now"
        updated = stripe.Subscription.modify(subscription.id, **modify_kwargs)
        return _stripe_action_url_from_latest_invoice(updated)

    if dedicated_item is not None:
        stripe.Subscription.modify(
            subscription.id,
            items=[{"id": dedicated_item.get("id"), "deleted": True}],
            proration_behavior="always_invoice",
        )
    return None


def handle_console_billing_update(request: HttpRequest) -> tuple[dict[str, object], int]:
    """Return (payload, status_code) for the console JSON billing update endpoint."""

    def _parse_payload():
        try:
            return json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return None

    def _resolve_owner(payload_dict):
        desired_owner_type = (payload_dict.get("ownerType") or "").strip().lower()
        desired_org_id = (payload_dict.get("organizationId") or "").strip()

        resolved_context = build_console_context(request)
        if resolved_context.current_context.type == "organization":
            membership = resolved_context.current_membership
            if membership is None:
                raise BillingUpdateError("org_access_lost", status=403)
            if membership.role not in BILLING_MANAGE_ROLES:
                raise BillingUpdateError("forbidden", status=403)
            owner_obj = membership.org
            if desired_owner_type and desired_owner_type != "organization":
                raise BillingUpdateError("context_mismatch", status=400)
            if desired_org_id and str(owner_obj.id) != desired_org_id:
                raise BillingUpdateError("context_mismatch", status=400)
            return owner_obj, "organization"

        owner_obj = request.user
        if desired_owner_type and desired_owner_type != "user":
            raise BillingUpdateError("context_mismatch", status=400)
        return owner_obj, "user"

    def _apply_seats(payload_dict, owner_obj, owner_type_str, response_dict):
        seats_target = payload_dict.get("seatsTarget", None)
        cancel_seat_schedule = bool(payload_dict.get("cancelSeatSchedule", False))
        if owner_type_str != "organization" or seats_target is None:
            return

        try:
            seats_target_int = int(seats_target)
        except (TypeError, ValueError):
            raise BillingUpdateError("invalid_seats_target", status=400)
        if seats_target_int < 0:
            raise BillingUpdateError("invalid_seats_target", status=400)

        billing = getattr(owner_obj, "billing", None)
        if billing is None:
            raise BillingUpdateError("missing_org_billing", status=400)

        seats_reserved = getattr(billing, "seats_reserved", 0) or 0
        if seats_target_int < seats_reserved:
            raise BillingUpdateError(
                "seats_below_reserved",
                status=400,
                detail="Cannot reduce seats below reserved. Remove members or invites first.",
            )

        stripe_settings = get_stripe_settings()
        seat_price_id = getattr(stripe_settings, "org_team_price_id", "") or ""
        if not seat_price_id:
            raise BillingUpdateError("seat_price_not_configured", status=400)

        subscription_id = getattr(billing, "stripe_subscription_id", None)
        if not subscription_id:
            if seats_target_int > 0:
                try:
                    _assign_stripe_api_key()
                    customer = get_or_create_stripe_customer(owner_obj)
                    if not customer or not getattr(customer, "id", None):
                        raise BillingUpdateError("stripe_customer_missing", status=400)

                    success_url = request.build_absolute_uri(reverse("billing")) + "?seats_success=1"
                    cancel_url = request.build_absolute_uri(reverse("billing")) + "?seats_cancelled=1"

                    session = stripe.checkout.Session.create(
                        customer=customer.id,
                        api_key=stripe.api_key,
                        mode="subscription",
                        success_url=success_url,
                        cancel_url=cancel_url,
                        allow_promotion_codes=True,
                        line_items=[{"price": seat_price_id, "quantity": seats_target_int}],
                        metadata={
                            "org_id": str(owner_obj.id),
                            "seat_requestor_id": str(request.user.id),
                        },
                    )
                    response_dict["redirectUrl"] = session.url
                except stripe.error.StripeError as exc:
                    logger.warning("Stripe error starting org seat checkout for org %s: %s", owner_obj.id, exc)
                    raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)
            return

        try:
            _assign_stripe_api_key()
            subscription = stripe.Subscription.retrieve(
                subscription_id,
                expand=["items.data.price", "latest_invoice.payment_intent", "latest_invoice"],
            )
        except stripe.error.StripeError as exc:
            logger.warning("Stripe error retrieving org subscription %s: %s", subscription_id, exc)
            raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)

        subscription_items = (subscription.get("items") or {}).get("data", []) or []
        licensed_item = None
        for item in subscription_items:
            price = item.get("price", {}) or {}
            usage_type = price.get("usage_type") or (price.get("recurring", {}) or {}).get("usage_type")
            price_id = price.get("id")
            if usage_type == "licensed" or (price_id and price_id == seat_price_id):
                licensed_item = item
                break
        if not licensed_item:
            raise BillingUpdateError("seat_item_missing", status=400)

        try:
            current_quantity = int(licensed_item.get("quantity") or 0)
        except (TypeError, ValueError):
            current_quantity = 0

        if seats_target_int == current_quantity and cancel_seat_schedule:
            schedule_id = getattr(billing, "pending_seat_schedule_id", "") or ""
            if schedule_id:
                try:
                    stripe.SubscriptionSchedule.release(schedule_id)
                except stripe.error.StripeError as exc:
                    logger.warning("Stripe error cancelling seat schedule %s: %s", schedule_id, exc)
                    raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)
                with transaction.atomic():
                    billing.pending_seat_quantity = None
                    billing.pending_seat_effective_at = None
                    billing.pending_seat_schedule_id = ""
                    billing.save(
                        update_fields=[
                            "pending_seat_quantity",
                            "pending_seat_effective_at",
                            "pending_seat_schedule_id",
                        ]
                    )
            return

        if seats_target_int > current_quantity:
            try:
                updated = stripe.Subscription.modify(
                    subscription_id,
                    items=[{"id": licensed_item.get("id"), "quantity": seats_target_int}],
                    metadata={
                        **(subscription.get("metadata") or {}),
                        "seat_requestor_id": str(request.user.id),
                    },
                    proration_behavior="always_invoice",
                    payment_behavior="pending_if_incomplete",
                    expand=["latest_invoice.payment_intent", "latest_invoice"],
                )
            except stripe.error.StripeError as exc:
                logger.warning("Stripe error updating org seats for subscription %s: %s", subscription_id, exc)
                raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)
            action_url = _stripe_action_url_from_latest_invoice(updated)
            if action_url:
                response_dict["stripeActionUrl"] = action_url
            return

        if seats_target_int < current_quantity:
            try:
                schedule_id = subscription.get("schedule") or getattr(billing, "pending_seat_schedule_id", "")
                if schedule_id:
                    stripe.SubscriptionSchedule.release(schedule_id)
                    with transaction.atomic():
                        billing.pending_seat_quantity = None
                        billing.pending_seat_effective_at = None
                        billing.pending_seat_schedule_id = ""
                        billing.save(
                            update_fields=[
                                "pending_seat_quantity",
                                "pending_seat_effective_at",
                                "pending_seat_schedule_id",
                            ]
                        )

                current_phase_items: list[dict[str, object]] = []
                next_phase_items: list[dict[str, object]] = []
                for item in subscription_items:
                    price = item.get("price", {}) or {}
                    price_id = price.get("id")
                    if not price_id:
                        continue
                    usage_type = price.get("usage_type") or (price.get("recurring", {}) or {}).get("usage_type")
                    is_seat_item = (
                        item is licensed_item
                        or usage_type == "licensed"
                        or (price_id and price_id == seat_price_id)
                    )
                    try:
                        quantity = int(item.get("quantity") or 0)
                    except (TypeError, ValueError):
                        quantity = 0

                    current_payload: dict[str, object] = {"price": price_id}
                    next_payload: dict[str, object] = {"price": price_id}

                    if is_seat_item:
                        current_payload["quantity"] = current_quantity
                        next_payload["quantity"] = seats_target_int
                    elif usage_type != "metered" and quantity > 0:
                        current_payload["quantity"] = quantity
                        next_payload["quantity"] = quantity

                    current_phase_items.append(current_payload)
                    next_phase_items.append(next_payload)

                current_period_start_ts = subscription.get("current_period_start")
                current_period_end_ts = subscription.get("current_period_end")

                phases: list[dict[str, object]] = [
                    {"items": current_phase_items, "proration_behavior": "none"},
                    {"items": next_phase_items, "proration_behavior": "none"},
                ]
                if current_period_start_ts:
                    phases[0]["start_date"] = int(current_period_start_ts)
                if current_period_end_ts:
                    period_end_int = int(current_period_end_ts)
                    phases[0]["end_date"] = period_end_int
                    phases[1]["start_date"] = period_end_int

                metadata = {
                    "org_id": str(owner_obj.id),
                    "seat_requestor_id": str(request.user.id),
                    "seat_target_quantity": str(seats_target_int),
                }

                schedule = stripe.SubscriptionSchedule.create(from_subscription=subscription.get("id"))
                stripe.SubscriptionSchedule.modify(
                    getattr(schedule, "id", ""),
                    phases=phases,
                    end_behavior="release",
                    metadata=metadata,
                )

                effective_at = None
                if current_period_end_ts:
                    try:
                        effective_at = datetime.fromtimestamp(int(current_period_end_ts), tz=dt_timezone.utc)
                    except (TypeError, ValueError, OSError):
                        effective_at = None

                with transaction.atomic():
                    billing.pending_seat_quantity = seats_target_int
                    billing.pending_seat_effective_at = effective_at
                    billing.pending_seat_schedule_id = getattr(schedule, "id", "") or ""
                    billing.save(
                        update_fields=[
                            "pending_seat_quantity",
                            "pending_seat_effective_at",
                            "pending_seat_schedule_id",
                        ]
                    )
            except stripe.error.StripeError as exc:
                logger.warning("Stripe error scheduling seat reduction for org %s: %s", owner_obj.id, exc)
                raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)

    def _apply_plan(payload_dict, owner_obj, owner_type_str, response_dict):
        plan_target = (payload_dict.get("planTarget") or "").strip().lower()
        if not plan_target:
            return
        if owner_type_str != "user":
            raise BillingUpdateError("invalid_owner_for_plan_change", status=400)

        # Prevent mixing plan switches with other changes; Stripe behavior differs for each flow.
        if payload_dict.get("addonQuantities") or payload_dict.get("dedicatedIps"):
            raise BillingUpdateError("plan_change_must_be_separate", status=400)

        if plan_target not in {"startup", "scale"}:
            raise BillingUpdateError("invalid_plan_target", status=400)

        stripe_settings = get_stripe_settings()
        if plan_target == "startup":
            licensed_price_id = getattr(stripe_settings, "startup_price_id", "") or ""
            metered_price_id = getattr(stripe_settings, "startup_additional_task_price_id", "") or ""
        else:
            licensed_price_id = getattr(stripe_settings, "scale_price_id", "") or ""
            metered_price_id = getattr(stripe_settings, "scale_additional_task_price_id", "") or ""

        if not licensed_price_id:
            raise BillingUpdateError("plan_not_configured", status=400)

        try:
            customer = get_or_create_stripe_customer(owner_obj)
            if not customer or not getattr(customer, "id", None):
                raise BillingUpdateError("stripe_customer_missing", status=400)

            updated, action = ensure_single_individual_subscription(
                str(customer.id),
                licensed_price_id=licensed_price_id,
                metered_price_id=metered_price_id or None,
                metadata={
                    "plan_target": plan_target,
                    "plan_requestor_id": str(request.user.id),
                    "source": "console_react_billing_update",
                },
                idempotency_key=f"console-plan-{customer.id}-{plan_target}-{request.user.id}",
                create_if_missing=False,
            )
            if action == "absent" or not updated:
                logger.warning(
                    "Plan change requested but no active subscription found for user %s (target=%s, action=%s)",
                    getattr(owner_obj, "id", None),
                    plan_target,
                    action,
                )
                raise BillingUpdateError("no_active_subscription", status=400, detail=SUPPORT_DETAIL)

            updated_id = updated.get("id") if isinstance(updated, Mapping) else None
            if not updated_id:
                raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)

            _assign_stripe_api_key()
            refreshed = stripe.Subscription.retrieve(
                updated_id,
                expand=["latest_invoice.payment_intent", "latest_invoice"],
            )
            action_url = _stripe_action_url_from_latest_invoice(refreshed)
            if action_url:
                response_dict["stripeActionUrl"] = action_url
        except stripe.error.StripeError as exc:
            logger.warning("Stripe error changing plan for user %s: %s", getattr(owner_obj, "id", None), exc)
            raise BillingUpdateError("stripe_error", status=400, detail=SUPPORT_DETAIL)

    payload = _parse_payload()
    if payload is None or not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_json"}, 400

    try:
        owner, owner_type = _resolve_owner(payload)

        if not stripe_status().enabled:
            raise BillingUpdateError("stripe_disabled", status=404)

        response_payload: dict[str, object] = {"ok": True}

        _apply_plan(payload, owner, owner_type, response_payload)
        _apply_seats(payload, owner, owner_type, response_payload)

        addon_quantities = payload.get("addonQuantities") or {}
        dedicated_changes = payload.get("dedicatedIps") or {}
        if owner_type == "organization" and (addon_quantities or dedicated_changes):
            billing = getattr(owner, "billing", None)
            if billing is None or getattr(billing, "purchased_seats", 0) <= 0:
                raise BillingUpdateError(
                    "seats_required",
                    status=400,
                    detail="Purchase at least one seat before managing add-ons or dedicated IPs.",
                )

        if addon_quantities:
            action_url = apply_addon_price_quantities(
                owner,
                owner_type,
                desired_quantities=addon_quantities,
                created_via="console_react_billing_update",
                end_trial_on_purchase=True,
            )
            if action_url:
                response_payload["stripeActionUrl"] = action_url

        if dedicated_changes:
            if not isinstance(dedicated_changes, Mapping):
                raise BillingUpdateError("invalid_dedicated_ips", status=400)
            try:
                add_quantity = int(dedicated_changes.get("addQuantity") or 0)
            except (TypeError, ValueError):
                raise BillingUpdateError("invalid_dedicated_ips", status=400)
            remove_proxy_ids = dedicated_changes.get("removeProxyIds") or []
            if not isinstance(remove_proxy_ids, list):
                raise BillingUpdateError("invalid_dedicated_ips", status=400)
            unassign_proxy_ids = {
                str(pid).strip()
                for pid in (dedicated_changes.get("unassignProxyIds") or [])
                if str(pid).strip()
            }
            action_url = apply_dedicated_ip_changes(
                owner,
                owner_type,
                add_quantity=add_quantity,
                remove_proxy_ids=[str(pid) for pid in remove_proxy_ids],
                unassign_proxy_ids=unassign_proxy_ids,
            )
            if action_url:
                response_payload["stripeActionUrl"] = action_url

        return response_payload, 200
    except BillingUpdateError as exc:
        return _error_payload(exc)
    except Exception:
        logger.exception(
            "Unhandled error in console billing update for user %s",
            getattr(getattr(request, "user", None), "id", None),
        )
        return {"ok": False, "error": "server_error", "detail": SUPPORT_DETAIL}, 500
