import os
import logging

from celery import shared_task
from django.utils import timezone

from config.plans import PLAN_CONFIG
from constants.plans import PlanNamesChoices
from observability import traced

from tasks.services import TaskCreditService
from util.subscription_helper import (
    get_users_due_for_monthly_grant,
    filter_users_without_active_subscription
)

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

        # Get users with credits expiring soon
        users = get_users_due_for_monthly_grant()

        # Filter to those without an active subscription
        users_without_subscription = filter_users_without_active_subscription(users)

        # Get the number of free credits from the current free plan
        free_plan_credits = int(os.getenv("FREE_PLAN_TASK_CREDITS", 100))

        free_plan = PLAN_CONFIG[PlanNamesChoices.FREE]

        for user in users_without_subscription:
            # Get their current task credit entry, if any.
            current_credit = TaskCreditService.get_current_task_credit(user).first()

            # @var current_credit: TaskCredit
            grant_date = current_credit.expiration_date if current_credit else timezone.now()

            TaskCreditService.grant_subscription_credits(
                user,
                plan=free_plan,
                grant_date=grant_date
            )