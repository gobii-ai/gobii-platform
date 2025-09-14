from __future__ import annotations

from celery import shared_task
from datetime import datetime, timedelta, time as dt_time
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from billing.services import BillingService
from util.subscription_helper import get_active_subscription, report_task_usage_to_stripe
from api.models import BrowserUseAgentTask, PersistentAgentStep, MeteringBatch

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
        period_start_date, period_end_date = BillingService.get_current_billing_period_for_user(user)

        # Detect any existing pending batch for this user within this period
        pending_task_keys = (
            BrowserUseAgentTask.objects
            .filter(
                user_id=user.id,
                metered=False,
                meter_batch_key__isnull=False,
                created_at__gte=start_dt,
                created_at__lt=end_dt,
            )
            .values_list("meter_batch_key", flat=True)
            .distinct()
        )
        pending_step_keys = (
            PersistentAgentStep.objects
            .filter(
                agent__user_id=user.id,
                metered=False,
                meter_batch_key__isnull=False,
                created_at__gte=start_dt,
                created_at__lt=end_dt,
            )
            .values_list("meter_batch_key", flat=True)
            .distinct()
        )

        pending_keys = {k for k in pending_task_keys if k} | {k for k in pending_step_keys if k}
        batch_key = None

        if pending_keys:
            batch_key = sorted(pending_keys)[0]
        else:
            # Create a new batch by reserving unmetered rows
            batch_key = uuid.uuid4().hex

            candidate_tasks = BrowserUseAgentTask.objects.filter(
                user_id=user.id,
                metered=False,
                meter_batch_key__isnull=True,
                created_at__gte=start_dt,
                created_at__lt=end_dt,
            )
            candidate_steps = PersistentAgentStep.objects.filter(
                agent__user_id=user.id,
                metered=False,
                meter_batch_key__isnull=True,
                created_at__gte=start_dt,
                created_at__lt=end_dt,
            )

            buat_ids = list(candidate_tasks.values_list('id', flat=True))
            step_ids = list(candidate_steps.values_list('id', flat=True))

            if not buat_ids and not step_ids:
                # Nothing to do for this user
                continue

            # Reserve rows for this batch
            BrowserUseAgentTask.objects.filter(id__in=buat_ids, meter_batch_key__isnull=True).update(meter_batch_key=batch_key)
            PersistentAgentStep.objects.filter(id__in=step_ids, meter_batch_key__isnull=True).update(meter_batch_key=batch_key)

        # Compute totals for the reserved batch only
        batch_tasks_qs = BrowserUseAgentTask.objects.filter(
            user_id=user.id,
            metered=False,
            meter_batch_key=batch_key,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )
        batch_steps_qs = PersistentAgentStep.objects.filter(
            agent__user_id=user.id,
            metered=False,
            meter_batch_key=batch_key,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )

        total_buat = batch_tasks_qs.aggregate(total=Coalesce(Sum("credits_cost"), Decimal("0")))['total']
        total_steps = batch_steps_qs.aggregate(total=Coalesce(Sum("credits_cost"), Decimal("0")))['total']

        total = (total_buat or Decimal("0")) + (total_steps or Decimal("0"))
        rounded = int(Decimal(total).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

        try:
            if rounded > 0:
                # Report a single meter event per user with idempotency key tied to the reserved batch
                idem_key = f"meter:{user.id}:{batch_key}"

                # Upsert metering batch record for audit
                MeteringBatch.objects.update_or_create(
                    batch_key=batch_key,
                    defaults={
                        'user_id': user.id,
                        'idempotency_key': idem_key,
                        'period_start': period_start_date,
                        'period_end': period_end_date,
                        'total_credits': total,
                        'rounded_quantity': rounded,
                    }
                )

                meter_event = report_task_usage_to_stripe(user, quantity=rounded, idempotency_key=idem_key)

                # Record Stripe event id for audit (coerce to string to avoid expression resolution on mocks)
                try:
                    event_id = getattr(meter_event, 'id', None)
                    if event_id is not None and not isinstance(event_id, (str, int)):
                        event_id = str(event_id)
                    MeteringBatch.objects.filter(batch_key=batch_key).update(
                        stripe_event_id=event_id
                    )
                except Exception:
                    logger.exception("Failed to store Stripe meter event id for user %s batch %s", user.id, batch_key)

                # Mark all rows in this reserved batch as metered, but preserve meter_batch_key for audit
                updated_tasks = batch_tasks_qs.update(metered=True)
                updated_steps = batch_steps_qs.update(metered=True)

                logger.info(
                    "Rollup metered user=%s batch=%s total=%s rounded=%s tasks=%s steps=%s",
                    user.id, batch_key, str(total), rounded, updated_tasks, updated_steps,
                )
                processed_users += 1
            else:
                # No billable units yet. If last day of period, finalize and mark; else release reservation.
                today = timezone.now().date()
                if today >= period_end_date:
                    # Upsert batch record noting finalize-zero
                    idem_key = f"meter:{user.id}:{batch_key}"
                    MeteringBatch.objects.update_or_create(
                        batch_key=batch_key,
                        defaults={
                            'user_id': user.id,
                            'idempotency_key': idem_key,
                            'period_start': period_start_date,
                            'period_end': period_end_date,
                            'total_credits': total,
                            'rounded_quantity': rounded,
                        }
                    )

                    updated_tasks = batch_tasks_qs.update(metered=True)
                    updated_steps = batch_steps_qs.update(metered=True)
                    logger.info(
                        "Rollup finalize (zero) user=%s batch=%s total=%s updated_tasks=%s updated_steps=%s",
                        user.id, batch_key, str(total), updated_tasks, updated_steps,
                    )
                    processed_users += 1
                else:
                    # Release reservation to allow accumulation in later runs
                    released_tasks = batch_tasks_qs.update(meter_batch_key=None)
                    released_steps = batch_steps_qs.update(meter_batch_key=None)
                    logger.info(
                        "Rollup carry-forward user=%s batch=%s total=%s rounded=%s (released tasks=%s steps=%s)",
                        user.id, batch_key, str(total), rounded, released_tasks, released_steps,
                    )
                    processed_users += 1
        except Exception:
            logger.exception("Failed rollup metering for user %s (batch=%s)", user.id, batch_key)

    logger.info("Rollup metering: finished processed_users=%s", processed_users)
    return processed_users
