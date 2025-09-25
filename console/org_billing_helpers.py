"""Utility helpers for assembling organization billing context for the console."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.utils import timezone

from billing.services import BillingService
from tasks.services import TaskCreditService
from util.subscription_helper import (
    allow_organization_extra_tasks,
    calculate_org_extra_tasks_used_during_subscription_period,
    get_organization_extra_task_limit,
    get_organization_plan,
    get_organization_task_credit_limit,
)


@dataclass(frozen=True)
class OrgBillingSeatInfo:
    purchased: int
    reserved: int
    available: int


@dataclass(frozen=True)
class OrgBillingPeriod:
    start: str
    end: str


def _serialize_decimal(value: Decimal | None) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(Decimal(value))


def build_org_billing_overview(organization) -> dict[str, Any]:
    """Return a serialisable dictionary describing the organization's billing state."""
    billing = getattr(organization, "billing", None)
    plan = get_organization_plan(organization)

    credits_qs = TaskCreditService.get_current_task_credit_for_owner(organization)
    total_granted = TaskCreditService.get_owner_task_credits_granted(
        organization,
        task_credits=credits_qs,
    )
    total_used = TaskCreditService.get_owner_task_credits_used(
        organization,
        task_credits=credits_qs,
    )
    available = TaskCreditService.calculate_available_tasks_for_owner(
        organization,
        task_credits=credits_qs,
    )

    entitlement = get_organization_task_credit_limit(organization)
    extra_limit = get_organization_extra_task_limit(organization)
    extra_used = calculate_org_extra_tasks_used_during_subscription_period(organization)
    extra_enabled = allow_organization_extra_tasks(organization)

    period_start, period_end = BillingService.get_current_billing_period_for_owner(organization)

    seat_info: OrgBillingSeatInfo | None = None
    if billing is not None:
        seat_info = OrgBillingSeatInfo(
            purchased=getattr(billing, "purchased_seats", 0) or 0,
            reserved=billing.seats_reserved,
            available=billing.seats_available,
        )

    cancel_at = None
    cancel_at_period_end = False
    if billing is not None and getattr(billing, "cancel_at", None):
        cancel_at = billing.cancel_at
        cancel_at_period_end = bool(getattr(billing, "cancel_at_period_end", False))

    max_extra_tasks = 0
    if billing is not None:
        max_extra_tasks = getattr(billing, "max_extra_tasks", 0) or 0

    now = timezone.now()

    return {
        "plan": {
            "id": plan.get("id"),
            "name": plan.get("name"),
            "currency": plan.get("currency", "usd"),
            "monthly_price": plan.get("price", 0),
            "credits_per_seat": plan.get("credits_per_seat"),
        },
        "seats": {
            "purchased": seat_info.purchased if seat_info else 0,
            "reserved": seat_info.reserved if seat_info else 0,
            "available": seat_info.available if seat_info else 0,
        },
        "period": OrgBillingPeriod(
            start=period_start.strftime("%B %d, %Y"),
            end=period_end.strftime("%B %d, %Y"),
        ).__dict__,
        "credits": {
            "granted": _serialize_decimal(total_granted),
            "used": _serialize_decimal(total_used),
            "available": _serialize_decimal(available),
            "entitlement": entitlement,
        },
        "extra_tasks": {
            "limit": extra_limit,
            "used_this_period": extra_used,
            "enabled": extra_enabled,
            "configured_limit": max_extra_tasks,
        },
        "billing_record": {
            "has_record": billing is not None,
            "stripe_customer_id": getattr(billing, "stripe_customer_id", None),
            "stripe_subscription_id": getattr(billing, "stripe_subscription_id", None),
            "cancel_at": cancel_at,
            "cancel_at_period_end": cancel_at_period_end,
            "updated_at": getattr(billing, "updated_at", now) if billing else now,
        },
    }
