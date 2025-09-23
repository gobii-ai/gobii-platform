from __future__ import annotations

from celery import shared_task
from datetime import datetime, date as dt_date, timedelta, time as dt_time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from numbers import Number
from typing import Any, Mapping

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date

from billing.services import BillingService
from util.subscription_helper import (
    get_active_subscription,
    report_task_usage_to_stripe,
    report_organization_task_usage_to_stripe,
)
from api.models import BrowserUseAgentTask, PersistentAgentStep, MeteringBatch, Organization

import logging

logger = logging.getLogger(__name__)


def _extract_subscription_value(sub: Any, key: str) -> Any:
    """Read a subscription attribute from Stripe payloads or direct attributes."""
    source = getattr(sub, "stripe_data", None)
    if source:
        if isinstance(source, Mapping):
            if key in source:
                return source[key]
        try:
            return getattr(source, key)
        except AttributeError:
            pass
    return getattr(sub, key, None)


def _period_bounds_for_owner(owner) -> tuple[tuple[datetime, datetime], tuple[dt_date, dt_date]]:
    """Return ([start_dt, end_dt_exclusive], [start_date, end_date]) for owner's billing period."""
    owner_meta = getattr(owner, "_meta", None)
    if owner_meta and owner_meta.model_name == "organization":
        start_date, end_date = BillingService.get_current_billing_period_for_owner(owner)
    else:
        start_date, end_date = BillingService.get_current_billing_period_for_user(owner)
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, dt_time.min), tz)
    end_exclusive = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), dt_time.min), tz)
    return (start_dt, end_exclusive), (start_date, end_date)


def _period_bounds_for_user(user) -> tuple[datetime, datetime]:
    """Return timezone-aware [start, end) datetimes for the user's current billing period."""
    (start_dt, end_exclusive), _ = _period_bounds_for_owner(user)
    return start_dt, end_exclusive


def _to_aware_dt(value, *, as_start: bool) -> datetime | None:
    """Try to coerce value to a timezone-aware datetime.

    Returns None if value is not a supported type or cannot be parsed.

    Supported:
    - datetime: ensure tz-aware (assume current TZ if naive)
    - date: convert to midnight start; for end bound, use next day's midnight (exclusive)
    - str: try parse_datetime; if None, parse_date and convert similarly
    """
    tz = timezone.get_current_timezone()
    dt: datetime | None = None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, dt_date):
        base = datetime.combine(value, dt_time.min)
        dt = base if as_start else (base + timedelta(days=1))
    elif isinstance(value, (str, Number)):
        text = str(value)
        parsed_dt = parse_datetime(text)
        if parsed_dt is not None:
            dt = parsed_dt
        else:
            parsed_d = parse_date(text)
            if parsed_d is not None:
                base = datetime.combine(parsed_d, dt_time.min)
                dt = base if as_start else (base + timedelta(days=1))
            else:
                try:
                    dt = datetime.fromtimestamp(float(text), tz=timezone.utc)
                except (TypeError, ValueError, OverflowError, OSError):
                    dt = None

    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, tz)
    return dt


def _rollup_for_user(user) -> int:
    """Process metering rollup for a single user. Returns 1 if attempted, else 0."""
    # Only non-free (active subscription) users are billed
    sub = get_active_subscription(user)
    if not sub:
        return 0

    # Use Stripe subscription period when available; otherwise fall back to local anchor bounds
    use_stripe_bounds = False
    current_period_start = _extract_subscription_value(sub, "current_period_start")
    current_period_end = _extract_subscription_value(sub, "current_period_end")

    if current_period_start is not None and current_period_end is not None:
        start_dt = _to_aware_dt(current_period_start, as_start=True)
        end_dt = _to_aware_dt(current_period_end, as_start=False)
        if start_dt is not None and end_dt is not None:
            use_stripe_bounds = True
            period_start_date = start_dt.date()
            period_end_date = end_dt.date()
    else:
        start_dt = end_dt = None

    if not use_stripe_bounds:
        (start_dt, end_dt), (period_start_date, period_end_date) = _period_bounds_for_owner(user)

    # Detect any existing pending batch for this user within this period
    pending_task_keys = (
        BrowserUseAgentTask.objects
        .filter(
            user_id=user.id,
            metered=False,
            meter_batch_key__isnull=False,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
            task_credit__additional_task=True,
            task_credit__organization__isnull=True,
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
            task_credit__additional_task=True,
            task_credit__organization__isnull=True,
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
            task_credit__additional_task=True,
            task_credit__organization__isnull=True,
        )
        candidate_steps = PersistentAgentStep.objects.filter(
            agent__user_id=user.id,
            metered=False,
            meter_batch_key__isnull=True,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
            task_credit__additional_task=True,
            task_credit__organization__isnull=True,
        )

        buat_ids = list(candidate_tasks.values_list('id', flat=True))
        step_ids = list(candidate_steps.values_list('id', flat=True))

        if not buat_ids and not step_ids:
            # Nothing to do for this user
            return 0

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
        task_credit__additional_task=True,
        task_credit__organization__isnull=True,
    )
    batch_steps_qs = PersistentAgentStep.objects.filter(
        agent__user_id=user.id,
        metered=False,
        meter_batch_key=batch_key,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
        task_credit__additional_task=True,
        task_credit__organization__isnull=True,
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
            batch_tasks_qs.update(metered=True)
            batch_steps_qs.update(metered=True)

            logger.info(
                "Rollup metered user=%s batch=%s total=%s rounded=%s",
                user.id, batch_key, str(total), rounded,
            )
            return 1
        else:
            # No billable units yet. If we've reached the end of Stripe period, finalize and mark; else release reservation.
            now_ts = timezone.now()
            if (use_stripe_bounds and now_ts >= end_dt) or ((not use_stripe_bounds) and (timezone.now().date() >= period_end_date)):
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

                batch_tasks_qs.update(metered=True)
                batch_steps_qs.update(metered=True)
                logger.info(
                    "Rollup finalize (zero) user=%s batch=%s total=%s",
                    user.id, batch_key, str(total),
                )
                return 1
            else:
                # Release reservation to allow accumulation in later runs
                batch_tasks_qs.update(meter_batch_key=None)
                batch_steps_qs.update(meter_batch_key=None)
                logger.info(
                    "Rollup carry-forward user=%s batch=%s total=%s rounded=%s",
                    user.id, batch_key, str(total), rounded,
                )
                return 1
    except Exception:
        logger.exception("Failed rollup metering for user %s (batch=%s)", user.id, batch_key)
        return 0


def _rollup_for_organization(org) -> int:
    """Process metering rollup for a single organization. Returns 1 if attempted, else 0."""
    billing = getattr(org, "billing", None)
    if not billing or not getattr(billing, "stripe_customer_id", None):
        return 0

    (start_dt, end_dt), (period_start_date, period_end_date) = _period_bounds_for_owner(org)

    pending_task_keys = (
        BrowserUseAgentTask.objects
        .filter(
            task_credit__organization_id=org.id,
            task_credit__additional_task=True,
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
            task_credit__organization_id=org.id,
            task_credit__additional_task=True,
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
        batch_key = uuid.uuid4().hex

        candidate_tasks = BrowserUseAgentTask.objects.filter(
            task_credit__organization_id=org.id,
            task_credit__additional_task=True,
            metered=False,
            meter_batch_key__isnull=True,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )
        candidate_steps = PersistentAgentStep.objects.filter(
            task_credit__organization_id=org.id,
            task_credit__additional_task=True,
            metered=False,
            meter_batch_key__isnull=True,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        )

        buat_ids = list(candidate_tasks.values_list('id', flat=True))
        step_ids = list(candidate_steps.values_list('id', flat=True))

        if not buat_ids and not step_ids:
            return 0

        BrowserUseAgentTask.objects.filter(id__in=buat_ids, meter_batch_key__isnull=True).update(meter_batch_key=batch_key)
        PersistentAgentStep.objects.filter(id__in=step_ids, meter_batch_key__isnull=True).update(meter_batch_key=batch_key)

    batch_tasks_qs = BrowserUseAgentTask.objects.filter(
        task_credit__organization_id=org.id,
        task_credit__additional_task=True,
        metered=False,
        meter_batch_key=batch_key,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    )
    batch_steps_qs = PersistentAgentStep.objects.filter(
        task_credit__organization_id=org.id,
        task_credit__additional_task=True,
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
            idem_key = f"meter:org:{org.id}:{batch_key}"

            MeteringBatch.objects.update_or_create(
                batch_key=batch_key,
                defaults={
                    'user': None,
                    'organization': org,
                    'idempotency_key': idem_key,
                    'period_start': period_start_date,
                    'period_end': period_end_date,
                    'total_credits': total,
                    'rounded_quantity': rounded,
                }
            )

            meter_event = report_organization_task_usage_to_stripe(org, quantity=rounded, idempotency_key=idem_key)

            try:
                event_id = getattr(meter_event, 'id', None)
                if event_id is not None and not isinstance(event_id, (str, int)):
                    event_id = str(event_id)
                MeteringBatch.objects.filter(batch_key=batch_key).update(
                    stripe_event_id=event_id
                )
            except Exception:
                logger.exception("Failed to store Stripe meter event id for organization %s batch %s", org.id, batch_key)

            batch_tasks_qs.update(metered=True)
            batch_steps_qs.update(metered=True)

            logger.info(
                "Rollup metered org=%s batch=%s total=%s rounded=%s",
                org.id, batch_key, str(total), rounded,
            )
            return 1
        else:
            now_ts = timezone.now()
            if now_ts.date() >= period_end_date:
                idem_key = f"meter:org:{org.id}:{batch_key}"
                MeteringBatch.objects.update_or_create(
                    batch_key=batch_key,
                    defaults={
                        'user': None,
                        'organization': org,
                        'idempotency_key': idem_key,
                        'period_start': period_start_date,
                        'period_end': period_end_date,
                        'total_credits': total,
                        'rounded_quantity': rounded,
                    }
                )

                batch_tasks_qs.update(metered=True)
                batch_steps_qs.update(metered=True)
                logger.info(
                    "Rollup finalize (zero) org=%s batch=%s total=%s",
                    org.id, batch_key, str(total),
                )
                return 1
            else:
                batch_tasks_qs.update(meter_batch_key=None)
                batch_steps_qs.update(meter_batch_key=None)
                logger.info(
                    "Rollup carry-forward org=%s batch=%s total=%s rounded=%s",
                    org.id, batch_key, str(total), rounded,
                )
                return 1
    except Exception:
        logger.exception("Failed rollup metering for organization %s (batch=%s)", org.id, batch_key)
        return 0


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
        .filter(
            metered=False,
            user__isnull=False,
            task_credit__additional_task=True,
            task_credit__organization__isnull=True,
        )
        .values_list("user_id", flat=True)
        .distinct()
    )
    step_users = (
        PersistentAgentStep.objects
        .filter(
            metered=False,
            task_credit__additional_task=True,
            task_credit__organization__isnull=True,
        )
        .values_list("agent__user_id", flat=True)
        .distinct()
    )

    user_ids = set(task_users) | set(step_users)
    logger.info("Rollup metering: candidate users=%s", len(user_ids))
    if not user_ids:
        logger.info("Rollup metering: no user candidates")

    org_task_orgs = (
        BrowserUseAgentTask.objects
        .filter(
            metered=False,
            task_credit__organization__isnull=False,
            task_credit__additional_task=True,
        )
        .values_list("task_credit__organization_id", flat=True)
        .distinct()
    )
    org_step_orgs = (
        PersistentAgentStep.objects
        .filter(
            metered=False,
            task_credit__organization__isnull=False,
            task_credit__additional_task=True,
        )
        .values_list("task_credit__organization_id", flat=True)
        .distinct()
    )
    org_ids = {oid for oid in org_task_orgs if oid} | {oid for oid in org_step_orgs if oid}
    logger.info("Rollup metering: candidate orgs=%s", len(org_ids))

    if not user_ids and not org_ids:
        logger.info("Rollup metering: no candidates; nothing to do")
        return 0

    processed_entities = 0
    users = User.objects.filter(id__in=user_ids)
    for user in users:
        processed_entities += _rollup_for_user(user)

    if org_ids:
        organizations = Organization.objects.filter(id__in=org_ids).select_related('billing')
        for org in organizations:
            processed_entities += _rollup_for_organization(org)

    logger.info("Rollup metering: finished processed_entities=%s", processed_entities)
    return processed_entities


@shared_task(bind=True, ignore_result=True, name="gobii_platform.api.tasks.rollup_usage_for_user")
def rollup_usage_for_user(self, user_id: int) -> int:
    """Run metering rollup for a single user id (admin/testing helper)."""
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.info("Rollup per-user: user %s not found", user_id)
        return 0
    return _rollup_for_user(user)
