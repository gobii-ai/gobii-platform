import logging
from datetime import datetime, timedelta
from typing import Mapping

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from billing.addons import AddonEntitlementService
from billing.services import BillingService
from constants.plans import PlanNamesChoices
from util.integrations import IntegrationDisabledError, stripe_status
from util.subscription_helper import (
    _ensure_stripe_ready,
    get_active_subscription,
    get_organization_plan,
    get_stripe_customer,
    get_user_max_contacts_per_agent,
    get_user_plan,
)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore

try:
    from djstripe.models import Invoice
except Exception:  # pragma: no cover - optional dependency
    Invoice = None  # type: ignore

logger = logging.getLogger(__name__)

MAX_ADDON_PACK_QUANTITY = 999
DELINQUENT_SUBSCRIPTION_STATUSES = {"past_due", "unpaid", "incomplete"}
ACTION_REQUIRED_INTENT_STATUSES = {"requires_action", "requires_payment_method"}


def _coerce_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _get_subscription_data(subscription) -> Mapping[str, object]:
    payload = getattr(subscription, "stripe_data", None)
    if isinstance(payload, Mapping):
        return payload
    return {}


def _resolve_latest_invoice_payload(subscription) -> Mapping[str, object]:
    subscription_data = _get_subscription_data(subscription)
    latest_invoice = subscription_data.get("latest_invoice")
    if isinstance(latest_invoice, Mapping):
        return latest_invoice
    if isinstance(latest_invoice, str) and Invoice is not None:
        invoice = Invoice.objects.filter(id=latest_invoice).first()
        invoice_payload = getattr(invoice, "stripe_data", None) if invoice is not None else None
        if isinstance(invoice_payload, Mapping):
            return invoice_payload
    return {}


def _is_invoice_retrying(latest_invoice_payload: Mapping[str, object], latest_invoice_status: str | None) -> bool:
    if latest_invoice_status != "open":
        return False
    if _safe_int(latest_invoice_payload.get("attempt_count")) < 1:
        return False
    return latest_invoice_payload.get("next_payment_attempt") is not None


def _resolve_candidate_subscription(owner, owner_type: str, customer_subscriptions: list):
    if owner_type == "organization":
        billing = getattr(owner, "billing", None)
        subscription_id = getattr(billing, "stripe_subscription_id", None) if billing is not None else None
        if isinstance(subscription_id, str) and subscription_id:
            for subscription in customer_subscriptions:
                if str(getattr(subscription, "id", "")) == subscription_id:
                    return subscription
    return customer_subscriptions[0] if customer_subscriptions else None


def _build_billing_status_payload(owner, owner_type: str, *, can_open_billing: bool, manage_billing_url: str | None) -> dict:
    status_payload = {
        "delinquent": False,
        "actionable": False,
        "reason": None,
        "subscriptionStatus": None,
        "latestInvoiceStatus": None,
        "paymentIntentStatus": None,
        "manageBillingUrl": manage_billing_url if can_open_billing else None,
    }
    if not stripe_status().enabled:
        return status_payload

    customer = get_stripe_customer(owner)
    if customer is None:
        return status_payload

    try:
        subscriptions = list(customer.subscriptions.all())
    except (AttributeError, TypeError):
        logger.exception("Failed to inspect customer subscriptions for billing status owner=%s", getattr(owner, "id", None))
        return status_payload
    if not subscriptions:
        return status_payload

    subscriptions.sort(
        key=lambda subscription: (
            _safe_int(_get_subscription_data(subscription).get("current_period_end")),
            _safe_int(_get_subscription_data(subscription).get("created")),
        ),
        reverse=True,
    )
    candidate_subscription = _resolve_candidate_subscription(owner, owner_type, subscriptions)
    if candidate_subscription is None:
        return status_payload

    subscription_data = _get_subscription_data(candidate_subscription)
    subscription_status = _coerce_status(subscription_data.get("status")) or _coerce_status(
        getattr(candidate_subscription, "status", None)
    )
    latest_invoice_payload = _resolve_latest_invoice_payload(candidate_subscription)
    latest_invoice_status = _coerce_status(latest_invoice_payload.get("status"))
    payment_intent_payload = latest_invoice_payload.get("payment_intent")
    payment_intent_status = _coerce_status(
        payment_intent_payload.get("status") if isinstance(payment_intent_payload, Mapping) else None
    )

    if subscription_status in DELINQUENT_SUBSCRIPTION_STATUSES:
        reason = subscription_status
    elif payment_intent_status in ACTION_REQUIRED_INTENT_STATUSES:
        reason = payment_intent_status
    elif _is_invoice_retrying(latest_invoice_payload, latest_invoice_status):
        reason = "invoice_retrying"
    else:
        reason = None

    if reason is None:
        return status_payload

    actionable = bool(can_open_billing and manage_billing_url)
    return {
        "delinquent": True,
        "actionable": actionable,
        "reason": reason,
        "subscriptionStatus": subscription_status,
        "latestInvoiceStatus": latest_invoice_status,
        "paymentIntentStatus": payment_intent_status,
        "manageBillingUrl": manage_billing_url if actionable else None,
    }


def _build_contact_cap_payload(agent) -> tuple[dict, bool]:
    from api.models import CommsAllowlistEntry, AgentAllowlistInvite

    active_count = CommsAllowlistEntry.objects.filter(
        agent=agent,
        is_active=True,
    ).count()
    pending_count = AgentAllowlistInvite.objects.filter(
        agent=agent,
        status=AgentAllowlistInvite.InviteStatus.PENDING,
    ).count()
    used = active_count + pending_count
    limit = get_user_max_contacts_per_agent(
        agent.user,
        organization=agent.organization,
    )
    unlimited = limit <= 0
    remaining = None if unlimited else max(0, limit - used)
    limit_reached = False if unlimited else limit > 0 and used >= limit

    payload = {
        "limit": None if unlimited else limit,
        "used": used,
        "remaining": remaining,
        "active": active_count,
        "pending": pending_count,
        "unlimited": unlimited,
    }
    return payload, limit_reached


def _build_pack_options(owner, owner_type: str, plan_id: str | None, addon_kind: str) -> list[dict]:
    addon_context = AddonEntitlementService.get_addon_context_for_owner(
        owner,
        owner_type,
        plan_id,
    )
    pack = addon_context.get(addon_kind) or {}
    options = pack.get("options") or []
    payload: list[dict] = []
    for option in options:
        price_id = option.get("price_id")
        if not price_id:
            continue
        payload.append(
            {
                "priceId": price_id,
                "delta": int(option.get("delta_value") or 0),
                "quantity": int(option.get("quantity") or 0),
                "unitAmount": option.get("unit_amount"),
                "currency": option.get("currency"),
                "priceDisplay": option.get("price_display") or "",
            }
        )
    return payload


def _build_contact_pack_options(owner, owner_type: str, plan_id: str | None) -> list[dict]:
    return _build_pack_options(owner, owner_type, plan_id, "contact_pack")


def _build_task_pack_options(owner, owner_type: str, plan_id: str | None) -> list[dict]:
    return _build_pack_options(owner, owner_type, plan_id, "task_pack")


def _update_pack_quantities(
    *,
    owner,
    owner_type: str,
    plan_id: str | None,
    quantities: dict,
    addon_kind: str,
    pack_label: str,
) -> tuple[bool, str | None, int]:
    if not stripe_status().enabled:
        return False, "Stripe billing is not available in this deployment.", 400
    if stripe is None:
        return False, "Stripe SDK not installed.", 400

    try:
        _ensure_stripe_ready()
    except IntegrationDisabledError as exc:
        return False, str(exc), 400

    label_lower = pack_label.lower()
    price_options = AddonEntitlementService.get_price_options(owner_type, plan_id, addon_kind)
    if not price_options:
        return False, f"{pack_label} pack pricing is not configured for your plan.", 400

    valid_price_ids = {cfg.price_id for cfg in price_options}
    desired_quantities: dict[str, int] = {}
    for price_id, raw_value in (quantities or {}).items():
        if price_id not in valid_price_ids:
            return False, f"That {label_lower} pack tier is not available for your plan.", 400
        try:
            qty = int(raw_value)
        except (TypeError, ValueError):
            return False, "Quantities must be whole numbers.", 400
        if qty < 0 or qty > MAX_ADDON_PACK_QUANTITY:
            return False, f"Quantities must be between 0 and {MAX_ADDON_PACK_QUANTITY}.", 400
        desired_quantities[price_id] = qty

    if not desired_quantities:
        return False, f"No {label_lower} pack quantities provided.", 400

    subscription = get_active_subscription(owner, preferred_plan_id=plan_id)
    if not subscription:
        return False, "No active subscription found.", 400

    try:
        stripe_subscription = stripe.Subscription.retrieve(
            subscription.id,
            expand=["customer", "items.data.price"],
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

        changes_made = False
        is_purchase = False
        is_trialing = (stripe_subscription.get("status") or "") == "trialing" if isinstance(stripe_subscription, Mapping) else False
        items_payload: list[dict[str, object]] = []
        for price_id, desired_qty in desired_quantities.items():
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
            changes_made = True

        updated_items = list(items_data) if isinstance(items_data, list) else []
        if changes_made:
            modify_kwargs = {
                "items": items_payload,
                "proration_behavior": "always_invoice",
                "expand": ["items.data.price"],
            }
            if not any(item.get("deleted") for item in items_payload):
                modify_kwargs["payment_behavior"] = "pending_if_incomplete"
            # Match billing page behavior: purchasing add-ons while trialing ends the trial immediately.
            if is_trialing and is_purchase:
                modify_kwargs["trial_end"] = "now"
            updated_subscription = stripe.Subscription.modify(subscription.id, **modify_kwargs)
            updated_items = (updated_subscription.get("items") or {}).get("data", []) if isinstance(updated_subscription, Mapping) else []
            if not isinstance(updated_items, list):
                updated_items = []

        try:
            period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)
            tz = timezone.get_current_timezone()
            period_start_dt = timezone.make_aware(datetime.combine(period_start, datetime.min.time()), tz)
            period_end_dt = timezone.make_aware(
                datetime.combine(period_end + timedelta(days=1), datetime.min.time()),
                tz,
            )
            AddonEntitlementService.sync_subscription_entitlements(
                owner=owner,
                owner_type=owner_type,
                plan_id=plan_id,
                subscription_items=updated_items,
                period_start=period_start_dt,
                period_end=period_end_dt,
                created_via="console_addons",
            )
        except Exception:
            logger.exception(
                "Failed to sync %s pack entitlements after add-on update for %s",
                label_lower,
                getattr(owner, "id", None) or owner,
            )
        return True, None, 200
    except stripe.error.StripeError as exc:
        logger.warning("Stripe API error while updating %s packs: %s", label_lower, exc)
        return False, f"A billing error occurred: {exc}", 400
    except Exception:
        logger.exception("Failed to update %s pack quantities for %s", label_lower, getattr(owner, "id", None) or owner)
        return False, "An unexpected error occurred while updating packs.", 500


def update_contact_pack_quantities(
    *,
    owner,
    owner_type: str,
    plan_id: str | None,
    quantities: dict,
) -> tuple[bool, str | None, int]:
    return _update_pack_quantities(
        owner=owner,
        owner_type=owner_type,
        plan_id=plan_id,
        quantities=quantities,
        addon_kind="contact_pack",
        pack_label="Contact",
    )


def update_task_pack_quantities(
    *,
    owner,
    owner_type: str,
    plan_id: str | None,
    quantities: dict,
) -> tuple[bool, str | None, int]:
    return _update_pack_quantities(
        owner=owner,
        owner_type=owner_type,
        plan_id=plan_id,
        quantities=quantities,
        addon_kind="task_pack",
        pack_label="Task",
    )


def build_agent_addons_payload(
    agent,
    owner=None,
    *,
    can_manage_billing: bool = False,
    can_open_billing: bool = False,
) -> dict:
    plan_payload = None
    upgrade_url = None
    manage_billing_url = None
    if agent.organization_id:
        plan_payload = get_organization_plan(agent.organization)
    else:
        plan_payload = get_user_plan(agent.user)
    plan_id = str(plan_payload.get("id", "")).lower() if plan_payload else ""
    plan_name = plan_payload.get("name") if plan_payload else ""
    plan_price = None
    if plan_payload:
        try:
            raw_price = plan_payload.get("price")
            plan_price = float(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            plan_price = None
    plan_currency = plan_payload.get("currency") if plan_payload else None
    is_free_plan = plan_id == PlanNamesChoices.FREE.value
    owner = owner or agent.organization or agent.user
    owner_type = "organization" if agent.organization_id else "user"

    if is_free_plan and settings.GOBII_PROPRIETARY_MODE:
        try:
            upgrade_url = reverse("proprietary:pricing")
        except NoReverseMatch:
            upgrade_url = None

    if can_open_billing:
        try:
            manage_billing_url = reverse("billing")
            if agent.organization_id:
                manage_billing_url = f"{manage_billing_url}?org_id={agent.organization_id}"
        except NoReverseMatch:
            manage_billing_url = None

    billing_status_payload = _build_billing_status_payload(
        owner,
        owner_type,
        can_open_billing=can_open_billing,
        manage_billing_url=manage_billing_url,
    )

    contact_cap_payload, contact_cap_reached = _build_contact_cap_payload(agent)
    contact_pack_options = (
        _build_contact_pack_options(owner, owner_type, plan_payload.get("id") if plan_payload else None)
        if can_manage_billing
        else []
    )
    task_pack_options = (
        _build_task_pack_options(owner, owner_type, plan_payload.get("id") if plan_payload else None)
        if can_manage_billing
        else []
    )

    subscription = get_active_subscription(owner, preferred_plan_id=plan_payload.get("id") if plan_payload else None)
    trial_end = getattr(subscription, "trial_end", None) if subscription is not None else None
    is_trialing = bool(subscription is not None and getattr(subscription, "status", "") == "trialing")

    return {
        "contactCap": contact_cap_payload,
        "status": {
            "contactCap": {
                "limitReached": contact_cap_reached,
            },
            "billing": billing_status_payload,
        },
        "contactPacks": {
            "options": contact_pack_options,
            "canManageBilling": bool(can_manage_billing),
        },
        "taskPacks": {
            "options": task_pack_options,
            "canManageBilling": bool(can_manage_billing),
        },
        "trial": {
            "isTrialing": bool(is_trialing),
            "trialEndsAtIso": trial_end.isoformat() if trial_end else None,
        },
        "plan": {
            "id": plan_id,
            "name": plan_name,
            "isFree": is_free_plan,
            "price": plan_price,
            "currency": plan_currency,
        },
        "upgradeUrl": upgrade_url,
        "manageBillingUrl": manage_billing_url,
    }
