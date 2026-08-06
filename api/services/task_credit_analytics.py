from decimal import Decimal, InvalidOperation
from enum import StrEnum

from django.db import transaction

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.analytics_billing import (
    AnalyticsAccessType,
    AnalyticsBillingContext,
    resolve_analytics_billing_context_safely,
)


class TaskCreditGrantOperation(StrEnum):
    CREATED = "created"
    REPLENISHED = "replenished"
    INCREASED = "increased"


class TaskCreditGrantSource(StrEnum):
    SUBSCRIPTION = "subscription"
    SUBSCRIPTION_CREATE = "subscription_create"
    SUBSCRIPTION_RENEWAL = "subscription_renewal"
    SUBSCRIPTION_TOPOFF = "subscription_topoff"
    SUBSCRIPTION_SEAT_INCREASE = "subscription_seat_increase"
    SIGNUP_BOOTSTRAP = "signup_bootstrap"
    MONTHLY_FREE_GRANT = "monthly_free_grant"
    DIRECT_TRIAL_PROMO = "direct_trial_promo"
    TASK_PACK_ENTITLEMENT = "task_pack_entitlement"
    REFERRAL = "referral"
    STAFF_CONSOLE = "staff_console"
    LEGACY_STAFF_ENDPOINT = "legacy_staff_endpoint"
    DJANGO_ADMIN = "django_admin"
    ADMIN_BULK_PLAN = "admin_bulk_plan"
    ADMIN_BULK_USER_IDS = "admin_bulk_user_ids"


def _positive_credit_amount(value) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return amount


def _grant_owner(task_credit):
    if task_credit.organization_id:
        organization = task_credit.organization
        return organization, organization.created_by
    return task_credit.user, task_credit.user


def capture_task_credit_billing_context(task_credit) -> AnalyticsBillingContext:
    """Capture billing state now so a later on-commit send cannot change it."""
    billing_owner, analytics_user = _grant_owner(task_credit)
    account_id = (
        str(billing_owner.pk)
        if task_credit.organization_id
        else f"user:{billing_owner.pk}"
    )
    return resolve_analytics_billing_context_safely(
        analytics_user.pk,
        organization_id=account_id,
        use_request_cache=False,
    )


def track_task_credit_grant(
    task_credit,
    *,
    credits_granted,
    operation: TaskCreditGrantOperation,
    grant_source: TaskCreditGrantSource,
    automated: bool,
    grant_actor_user_id=None,
    billing_context: AnalyticsBillingContext | None = None,
) -> None:
    """Queue analytics for a committed, positive, usable credit issuance."""
    amount = _positive_credit_amount(credits_granted)
    if amount is None or task_credit.additional_task or task_credit.voided:
        return

    billing_owner, analytics_user = _grant_owner(task_credit)
    captured_billing_context = billing_context or capture_task_credit_billing_context(task_credit)
    owner_type = "organization" if task_credit.organization_id else "user"
    owner_id = task_credit.organization_id or task_credit.user_id

    properties = {
        "task_credit_id": str(task_credit.pk),
        "credits_granted": float(amount),
        "grant_type": str(task_credit.grant_type),
        "grant_source": str(grant_source),
        "grant_operation": str(operation),
        "automated": bool(automated),
        "credit_plan": str(task_credit.plan),
        "free_trial_start": bool(task_credit.free_trial_start),
        "owner_type": owner_type,
        "owner_id": str(owner_id),
        "is_trial": captured_billing_context.access_type_at_event == AnalyticsAccessType.TRIAL,
    }
    if task_credit.stripe_invoice_id:
        properties["stripe.invoice_id"] = task_credit.stripe_invoice_id
    if grant_actor_user_id is not None:
        actor_id = getattr(grant_actor_user_id, "pk", grant_actor_user_id)
        properties["grant_actor_user_id"] = str(actor_id)
    if task_credit.organization_id:
        properties = Analytics.with_org_properties(properties, organization=billing_owner)

    event_user_id = analytics_user.pk
    event_source = AnalyticsSource.API if automated else AnalyticsSource.CONSOLE

    def send_event() -> None:
        Analytics.track_event(
            user_id=event_user_id,
            event=AnalyticsEvent.TASK_CREDITS_GRANTED,
            source=event_source,
            properties=properties,
            billing_context=captured_billing_context,
        )

    transaction.on_commit(send_event)
