from decimal import Decimal, InvalidOperation

from django.urls import reverse

from api.models import Organization, UserBilling
from api.services.dedicated_proxy_service import DedicatedProxyService, is_multi_assign_enabled
from billing.addons import AddonEntitlementService
from billing.churnkey import build_churnkey_cancel_flow_config
from config.plans import PLAN_CONFIG, get_plan_config
from console.context_helpers import build_console_context
from console.extra_tasks_settings import derive_extra_tasks_settings
from console.org_billing_helpers import build_org_billing_overview
from console.role_constants import BILLING_MANAGE_ROLES
from constants.plans import PlanNamesChoices
from util.subscription_helper import (
    get_active_subscription,
    get_stripe_customer,
    get_subscription_base_price,
    reconcile_user_plan_from_stripe,
)


def _serialize_addon_context(addon_context: dict) -> dict[str, object]:
    if not addon_context:
        return {
            "kinds": {},
            "totals": {"amountCents": 0, "currency": "", "amountDisplay": ""},
        }

    def _kind_payload(kind: str) -> dict[str, object]:
        options = ((addon_context or {}).get(kind) or {}).get("options") or []
        payload_options: list[dict[str, object]] = []
        for opt in options:
            payload_options.append(
                {
                    "priceId": opt.get("price_id"),
                    "quantity": opt.get("quantity") or 0,
                    "delta": opt.get("delta_value") or 0,
                    "unitAmount": opt.get("unit_amount"),
                    "currency": opt.get("currency") or "",
                    "priceDisplay": opt.get("price_display") or "",
                }
            )
        return {"options": payload_options}

    totals = (addon_context or {}).get("totals") or {}
    return {
        "kinds": {
            "taskPack": _kind_payload("task_pack"),
            "contactPack": _kind_payload("contact_pack"),
            "browserTaskPack": _kind_payload("browser_task_limit"),
            "advancedCaptcha": _kind_payload("advanced_captcha_resolution"),
        },
        "totals": {
            "amountCents": totals.get("amount_cents") or 0,
            "currency": totals.get("currency") or "",
            "amountDisplay": totals.get("amount_display") or "",
        },
    }


def _serialize_dedicated_proxies(proxies_qs) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for proxy in proxies_qs:
        browser_agents = list(getattr(proxy, "browser_agents").all())
        assigned_agents = [
            getattr(browser_agent, "persistent_agent", None)
            for browser_agent in browser_agents
            if getattr(browser_agent, "persistent_agent", None) is not None
        ]
        assigned = [{"id": str(agent.id), "name": agent.name} for agent in assigned_agents]
        payload.append(
            {
                "id": str(proxy.id),
                "label": proxy.static_ip or proxy.host,
                "name": proxy.name,
                "staticIp": proxy.static_ip,
                "host": proxy.host,
                "assignedAgents": assigned,
            }
        )
    return payload


def _resolve_dedicated_ip_pricing(plan):
    plan = plan or {}
    currency = plan.get("currency")
    unit_price = plan.get("dedicated_ip_price")
    plan_id = plan.get("id")

    if unit_price is None and plan_id:
        fallback = PLAN_CONFIG.get(str(plan_id).lower())
        if fallback:
            if unit_price is None:
                unit_price = fallback.get("dedicated_ip_price")
            if not currency:
                currency = fallback.get("currency", currency)

    if unit_price is None:
        unit_price = 0

    try:
        price_decimal = Decimal(str(unit_price))
    except (InvalidOperation, TypeError, ValueError):
        price_decimal = Decimal("0")

    normalized_currency = (currency or "USD").upper()
    return price_decimal, normalized_currency


def build_billing_initial_data(request) -> dict[str, object]:
    context_info = build_console_context(request)
    current_context = context_info.current_context

    if current_context.type == "organization" and current_context.id:
        organization = Organization.objects.select_related("billing").get(id=current_context.id)
        overview = build_org_billing_overview(organization)
        membership = context_info.current_membership
        can_manage_billing = bool(membership and membership.role in BILLING_MANAGE_ROLES)

        configured_limit = int(overview.get("extra_tasks", {}).get("configured_limit") or 0)
        extra_tasks_settings = derive_extra_tasks_settings(
            configured_limit,
            can_modify=can_manage_billing,
            endpoints={
                "loadUrl": reverse("get_billing_settings"),
                "updateUrl": reverse("update_billing_settings"),
            },
        )

        billing = getattr(organization, "billing", None)
        seat_purchase_required = bool(getattr(billing, "purchased_seats", 0) <= 0)
        has_stripe_subscription = bool(getattr(billing, "stripe_subscription_id", None))

        dedicated_proxies_qs = (
            DedicatedProxyService.allocated_proxies(organization)
            .select_related("dedicated_allocation")
            .prefetch_related("browser_agents__persistent_agent")
            .order_by("static_ip", "host", "port")
        )
        dedicated_allowed = (overview.get("plan") or {}).get("id") != PlanNamesChoices.FREE.value
        unit_price, price_currency = _resolve_dedicated_ip_pricing(overview.get("plan"))

        addon_context = AddonEntitlementService.get_addon_context_for_owner(
            organization,
            "organization",
            (overview.get("plan") or {}).get("id"),
        )
        org_addons_disabled = (not can_manage_billing) or (not has_stripe_subscription)

        org_plan_cfg = get_plan_config("org_team") or {}
        seat_unit_price_raw = org_plan_cfg.get("price_per_seat", org_plan_cfg.get("price", 0)) or 0
        try:
            seat_unit_price = float(Decimal(str(seat_unit_price_raw)))
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            seat_unit_price = 0.0
        seat_currency = (
            org_plan_cfg.get("currency")
            or (overview.get("plan") or {}).get("currency")
            or "USD"
        ).upper()

        pending_seats = overview.get("pending_seats") or {}
        pending_effective_at = pending_seats.get("effective_at")
        billing_record = overview.get("billing_record") or {}
        org_can_open_stripe = can_manage_billing and bool(billing_record.get("stripe_customer_id"))

        return {
            "contextType": "organization",
            "organization": {"id": str(organization.id), "name": organization.name},
            "canManageBilling": can_manage_billing,
            "plan": overview.get("plan") or {},
            "trial": {
                "isTrialing": False,
                "trialEndsAtIso": None,
            },
            "extraTasks": extra_tasks_settings,
            "paidSubscriber": (overview.get("seats") or {}).get("purchased", 0) > 0,
            "seats": {
                "purchased": (overview.get("seats") or {}).get("purchased", 0),
                "reserved": (overview.get("seats") or {}).get("reserved", 0),
                "available": (overview.get("seats") or {}).get("available", 0),
                "unitPrice": seat_unit_price,
                "currency": seat_currency,
                "pendingQuantity": pending_seats.get("quantity"),
                "pendingEffectiveAtIso": pending_effective_at.isoformat() if pending_effective_at is not None else None,
                "hasStripeSubscription": has_stripe_subscription,
            },
            "addons": _serialize_addon_context(addon_context),
            "addonsDisabled": bool(org_addons_disabled) or bool(seat_purchase_required),
            "dedicatedIps": {
                "allowed": bool(dedicated_allowed),
                "unitPrice": float(unit_price),
                "currency": price_currency,
                "multiAssign": bool(is_multi_assign_enabled()),
                "proxies": _serialize_dedicated_proxies(dedicated_proxies_qs),
            },
            "endpoints": {
                "updateUrl": reverse("console_billing_update"),
                "stripePortalUrl": (
                    reverse("organization_seat_portal", kwargs={"org_id": organization.id})
                    if org_can_open_stripe
                    else None
                ),
            },
        }

    subscription_plan = reconcile_user_plan_from_stripe(request.user) or {}
    subscription = get_active_subscription(
        request.user,
        preferred_plan_id=(subscription_plan or {}).get("id"),
        sync_with_stripe=True,
    )
    actual_price, actual_currency = get_subscription_base_price(subscription)

    if actual_price is not None or actual_currency:
        subscription_plan = subscription_plan.copy()
        if actual_price is not None:
            subscription_plan["price"] = float(actual_price)
        if actual_currency:
            subscription_plan["currency"] = actual_currency

    paid_subscriber = subscription is not None
    period_start_date = (
        subscription.current_period_start.strftime("%B %d, %Y")
        if paid_subscriber and getattr(subscription, "current_period_start", None)
        else None
    )
    period_end_date = (
        subscription.current_period_end.strftime("%B %d, %Y")
        if paid_subscriber and getattr(subscription, "current_period_end", None)
        else None
    )
    cancel_at = (
        subscription.cancel_at.strftime("%B %d, %Y")
        if paid_subscriber and getattr(subscription, "cancel_at", None)
        else None
    )
    cancel_at_period_end = bool(getattr(subscription, "cancel_at_period_end", False)) if paid_subscriber else False

    dedicated_allowed = (subscription_plan or {}).get("id") != PlanNamesChoices.FREE.value
    dedicated_proxies_qs = (
        DedicatedProxyService.allocated_proxies(request.user)
        .select_related("dedicated_allocation")
        .prefetch_related("browser_agents__persistent_agent")
        .order_by("static_ip", "host", "port")
    )
    unit_price, price_currency = _resolve_dedicated_ip_pricing(subscription_plan)

    addon_context = AddonEntitlementService.get_addon_context_for_owner(
        request.user,
        "user",
        subscription_plan.get("id"),
    )

    trial_end = getattr(subscription, "trial_end", None) if subscription is not None else None
    is_trialing = bool(subscription is not None and getattr(subscription, "status", "") == "trialing")
    user_billing, _ = UserBilling.objects.get_or_create(
        user=request.user,
        defaults={"max_extra_tasks": 0},
    )
    stripe_customer = get_stripe_customer(request.user)
    personal_can_open_stripe = bool(stripe_customer)
    churnkey_config = build_churnkey_cancel_flow_config(
        customer_id=getattr(stripe_customer, "id", None),
        subscription_id=getattr(subscription, "id", None),
        livemode=getattr(stripe_customer, "livemode", None),
    )
    personal_extra_limit = int(getattr(user_billing, "max_extra_tasks", 0) or 0)
    personal_extra_settings = derive_extra_tasks_settings(
        personal_extra_limit,
        can_modify=True,
        endpoints={
            "loadUrl": reverse("get_billing_settings"),
            "updateUrl": reverse("update_billing_settings"),
        },
    )

    return {
        "contextType": "personal",
        "canManageBilling": True,
        "paidSubscriber": bool(paid_subscriber),
        "plan": subscription_plan,
        "trial": {
            "isTrialing": bool(is_trialing),
            "trialEndsAtIso": trial_end.isoformat() if trial_end else None,
        },
        "extraTasks": personal_extra_settings,
        "periodStartDate": period_start_date,
        "periodEndDate": period_end_date,
        "cancelAt": cancel_at,
        "cancelAtPeriodEnd": cancel_at_period_end,
        "churnKey": churnkey_config,
        "addons": _serialize_addon_context(addon_context),
        "addonsDisabled": not paid_subscriber,
        "dedicatedIps": {
            "allowed": bool(dedicated_allowed),
            "unitPrice": float(unit_price),
            "currency": price_currency,
            "multiAssign": bool(is_multi_assign_enabled()),
            "proxies": _serialize_dedicated_proxies(dedicated_proxies_qs),
        },
        "endpoints": {
            "updateUrl": reverse("console_billing_update"),
            "cancelSubscriptionUrl": reverse("cancel_subscription"),
            "churnKeySyncUrl": reverse("sync_billing_subscription_state"),
            "resumeSubscriptionUrl": reverse("resume_subscription"),
            "stripePortalUrl": reverse("billing_portal") if personal_can_open_stripe else None,
        },
    }
