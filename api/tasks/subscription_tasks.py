import logging
from datetime import datetime, time, timedelta

from celery import shared_task
from django.utils import timezone
from django.apps import apps
from dateutil.relativedelta import relativedelta

from config.plans import PLAN_CONFIG
from constants.plans import PlanNamesChoices
from observability import traced

from tasks.services import TaskCreditService
from util.subscription_helper import (
    get_users_due_for_monthly_grant,
    filter_users_without_active_subscription
)
from billing.services import BillingService
from constants.grant_types import GrantTypeChoices

# --------------------------------------------------------------------------- #
#  Optional djstripe import
# --------------------------------------------------------------------------- #
try:
    import stripe
    from djstripe.models import Subscription

    DJSTRIPE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore
    Subscription = None  # type: ignore
    DJSTRIPE_AVAILABLE = False

logger = logging.getLogger(__name__)


@shared_task(name="api.tasks.grant_monthly_free_credits")
def grant_monthly_free_credits() -> None:
    """
    Grant free task credits to all users on the first day of each month. Gets the users by calling `get_users_with_credits_expiring_soon`,
    and then filtering it to only those who do not have an active subscription with `filter_users_without_active_subscription`.
    Then, it grants the free credits to those users for the number in the current free plan, with the grant date
    set the expiration date of the current entry. If no current entry (fail safe), it uses timezone.now(). Either way,
    the expiration date is set to 30 days from the grant date.
    """
    with traced("CREDITS Grant Monthly Free Credits"):
        if not DJSTRIPE_AVAILABLE:
            logger.warning("djstripe not available; skipping free credit grant")
            return

        # Get users who are due for their monthly grant based on billing cycle
        users = get_users_due_for_monthly_grant()
        logger.info("grant_monthly_free_credits: %d users due for monthly grant", len(users))

        # Filter to those without an active Stripe subscription (free plan users)
        users_without_subscription = filter_users_without_active_subscription(users)
        logger.info(
            "grant_monthly_free_credits: %d users without active subscription (will receive grant)",
            len(users_without_subscription),
        )

        free_plan = PLAN_CONFIG[PlanNamesChoices.FREE]
        TaskCredit = apps.get_model("api", "TaskCredit")
        today = timezone.now().date()
        current_tz = timezone.get_current_timezone()

        for user in users_without_subscription:
            grant_date = None
            expiration_date = None

            billing = getattr(user, "billing", None)
            billing_day = getattr(billing, "billing_cycle_anchor", None) if billing else None

            if billing_day is not None:
                try:
                    billing_day = int(billing_day)
                    period_start, period_end = BillingService.get_current_billing_period_from_day(
                        billing_day,
                        today,
                    )
                except (TypeError, ValueError):
                    billing_day = None
                else:
                    grant_date = timezone.make_aware(
                        datetime.combine(period_start, time.min),
                        timezone=current_tz,
                    )
                    next_period_start = period_end + timedelta(days=1)
                    expiration_date = timezone.make_aware(
                        datetime.combine(next_period_start, time.min),
                        timezone=current_tz,
                    )

            if grant_date is None:
                last_plan_credit = (
                    TaskCredit.objects.filter(
                        user=user,
                        grant_type=GrantTypeChoices.PLAN,
                        additional_task=False,
                        voided=False,
                    )
                    .order_by('-granted_date')
                    .first()
                )

                if last_plan_credit is not None:
                    grant_date = last_plan_credit.granted_date + relativedelta(months=1)
                    if expiration_date is None and last_plan_credit.expiration_date is not None:
                        expiration_date = last_plan_credit.expiration_date + relativedelta(months=1)
                else:
                    grant_date = timezone.now()

            if expiration_date is None:
                expiration_date = grant_date + relativedelta(months=1)

            TaskCreditService.grant_subscription_credits(
                user,
                plan=free_plan,
                grant_date=grant_date,
                expiration_date=expiration_date,
            )
