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
    get_user_max_contacts_per_agent,
    get_user_plan,
)

try:
    import stripe
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore

logger = logging.getLogger(__name__)


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


def _build_contact_pack_options(owner, owner_type: str, plan_id: str | None) -> list[dict]:
    addon_context = AddonEntitlementService.get_addon_context_for_owner(
        owner,
        owner_type,
        plan_id,
    )
    contact_pack = addon_context.get("contact_pack") or {}
    options = contact_pack.get("options") or []
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


def update_contact_pack_quantities(
    *,
    owner,
    owner_type: str,
    plan_id: str | None,
    quantities: dict,
) -> tuple[bool, str | None, int]:
    if not stripe_status().enabled:
        return False, "Stripe billing is not available in this deployment.", 400
    if stripe is None:
        return False, "Stripe SDK not installed.", 400

    try:
        _ensure_stripe_ready()
    except IntegrationDisabledError as exc:
        return False, str(exc), 400

    price_options = AddonEntitlementService.get_price_options(owner_type, plan_id, "contact_pack")
    if not price_options:
        return False, "Contact pack pricing is not configured for your plan.", 400

    valid_price_ids = {cfg.price_id for cfg in price_options}
    desired_quantities: dict[str, int] = {}
    for price_id, raw_value in (quantities or {}).items():
        if price_id not in valid_price_ids:
            return False, "That contact pack tier is not available for your plan.", 400
        try:
            qty = int(raw_value)
        except (TypeError, ValueError):
            return False, "Quantities must be whole numbers.", 400
        if qty < 0 or qty > 999:
            return False, "Quantities must be between 0 and 999.", 400
        desired_quantities[price_id] = qty

    if not desired_quantities:
        return False, "No contact pack quantities provided.", 400

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
        items_payload: list[dict[str, object]] = []
        for price_id, desired_qty in desired_quantities.items():
            current_qty = existing_qty.get(price_id, 0)
            if desired_qty == current_qty:
                continue
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
                "Failed to sync contact pack entitlements after add-on update for %s",
                getattr(owner, "id", None) or owner,
            )
        return True, None, 200
    except stripe.error.StripeError as exc:
        logger.warning("Stripe API error while updating contact packs: %s", exc)
        return False, f"A billing error occurred: {exc}", 400
    except Exception:
        logger.exception("Failed to update contact pack quantities for %s", getattr(owner, "id", None) or owner)
        return False, "An unexpected error occurred while updating contact packs.", 500


def build_agent_addons_payload(agent, owner=None, *, can_manage_billing: bool = False) -> dict:
    plan_payload = None
    upgrade_url = None
    if agent.organization_id:
        plan_payload = get_organization_plan(agent.organization)
    else:
        plan_payload = get_user_plan(agent.user)
    plan_id = str(plan_payload.get("id", "")).lower() if plan_payload else ""
    plan_name = plan_payload.get("name") if plan_payload else ""
    is_free_plan = plan_id == PlanNamesChoices.FREE.value
    owner = owner or agent.organization or agent.user
    owner_type = "organization" if agent.organization_id else "user"

    if is_free_plan and settings.GOBII_PROPRIETARY_MODE:
        try:
            upgrade_url = reverse("proprietary:pricing")
        except NoReverseMatch:
            upgrade_url = None

    contact_cap_payload, contact_cap_reached = _build_contact_cap_payload(agent)
    contact_pack_options = (
        _build_contact_pack_options(owner, owner_type, plan_payload.get("id") if plan_payload else None)
        if can_manage_billing
        else []
    )

    return {
        "contactCap": contact_cap_payload,
        "status": {
            "contactCap": {
                "limitReached": contact_cap_reached,
            },
        },
        "contactPacks": {
            "options": contact_pack_options,
            "canManageBilling": bool(can_manage_billing),
        },
        "plan": {
            "id": plan_id,
            "name": plan_name,
            "isFree": is_free_plan,
        },
        "upgradeUrl": upgrade_url,
    }
