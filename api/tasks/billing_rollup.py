from __future__ import annotations

from celery import shared_task
from datetime import datetime, timedelta, time as dt_time
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from billing.services import BillingService
from util.subscription_helper import get_active_subscription, report_task_usage_to_stripe
from api.models import BrowserUseAgentTask, PersistentAgentStep

import logging

logger = logging.getLogger(__name__)


def _period_bounds_for_user(user) -> tuple[datetime, datetime]:
    """Return timezone-aware [start, end) datetimes for the user's current billing period."""
    start_date, end_date = BillingService.get_current_billing_period_for_user(user)
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, dt_time.min), tz)
    end_exclusive = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), dt_time.min), tz)
    return start_dt, end_exclusive


@shared_task(bind=True, ignore_result=True, name="gobii_platform.api.tasks.rollup_and_meter_usage")
def rollup_and_meter_usage_task(self) -> int:
    """
    Aggregate unmetered fractional task usage for all paid users and report to Stripe.

    - Finds all unmetered `BrowserUseAgentTask` and `PersistentAgentStep` rows within
      each user's current billing period.
    - Sums `credits_cost` across both tables, rounds to the nearest whole integer.
    - Reports that integer quantity via Stripe meter event once per user.
    - Marks all included rows as metered.

    Returns the number of users for whom a rollup was attempted.
    """
    User = get_user_model()
    logger.info("Rollup metering: task start")

    # Identify candidate users with unmetered usage
    task_users = (
        BrowserUseAgentTask.objects
        .filter(metered=False, user__isnull=False)
        .values_list("user_id", flat=True)
        .distinct()
    )
    step_users = (
        PersistentAgentStep.objects
        .filter(metered=False)
        .values_list("agent__user_id", flat=True)
        .distinct()
    )

    user_ids = set(task_users) | set(step_users)
    logger.info("Rollup metering: candidate users=%s", len(user_ids))
    if not user_ids:
        logger.info("Rollup metering: no candidates; nothing to do")
        return 0

    processed_users = 0

    users = User.objects.filter(id__in=user_ids)
    for user in users:
        # Only non-free (active subscription) users are billed
        sub = get_active_subscription(user)
        if not sub:
            continue

        start_dt, end_dt = _period_bounds_for_user(user)

        # Collect unmetered usage within the period from both sources
        buat_qs = BrowserUseAgentTask.objects.filter(
            user_id=user.id,
            metered=False,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )

        step_qs = PersistentAgentStep.objects.filter(
            agent__user_id=user.id,
            metered=False,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )

        total_buat = buat_qs.aggregate(total=Coalesce(Sum("credits_cost"), Decimal("0")))['total']
        total_steps = step_qs.aggregate(total=Coalesce(Sum("credits_cost"), Decimal("0")))['total']

        total = (total_buat or Decimal("0")) + (total_steps or Decimal("0"))

        # Round to nearest whole integer using half-up semantics
        rounded = int(Decimal(total).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        # Evaluate the querysets to get a fixed list of IDs
        buat_ids = list(buat_qs.values_list('id', flat=True))
        step_ids = list(step_qs.values_list('id', flat=True))

        try:
            if rounded > 0:
                # Report a single meter event per user
                report_task_usage_to_stripe(user, quantity=rounded)

                # Mark all included rows as metered when we successfully bill a non-zero quantity
                updated_tasks = BrowserUseAgentTask.objects.filter(id__in=buat_ids).update(metered=True)
                updated_steps = PersistentAgentStep.objects.filter(id__in=step_ids).update(metered=True)

                logger.info(
                    "Rollup metering user=%s total=%s rounded=%s updated_tasks=%s updated_steps=%s",
                    user.id, str(total), rounded, updated_tasks, updated_steps,
                )
                processed_users += 1
            else:
                # Carry forward: do not mark rows yet so they can accumulate on subsequent runs
                # However, if we're at the last day of this user's billing period, finalize and mark
                today = timezone.now().date()
                _, period_end_date = BillingService.get_current_billing_period_for_user(user)
                if today >= period_end_date:
                    # Finalize the period with no billing event; mark rows as metered to avoid cross-period carryover
                    updated_tasks = buat_qs.update(metered=True)
                    updated_steps = step_qs.update(metered=True)
                    logger.info(
                        "Rollup finalize (zero) user=%s total=%s rounded=%s updated_tasks=%s updated_steps=%s",
                        user.id, str(total), rounded, updated_tasks, updated_steps,
                    )
                    processed_users += 1
                else:
                    logger.info(
                        "Rollup carry-forward user=%s total=%s rounded=%s (nothing metered/marked)",
                        user.id, str(total), rounded,
                    )
                    # processed_users still counts attempt
                    processed_users += 1
        except Exception:
            logger.exception("Failed rollup metering for user %s", user.id)

    logger.info("Rollup metering: finished processed_users=%s", processed_users)
    return processed_users
