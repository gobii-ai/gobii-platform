# platform/tasks/services.py
from django.contrib.auth.models import User
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models.aggregates import Sum
from django.db.models.expressions import F
from django.db.models import Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import timezone as dt_timezone
from django.utils.dateparse import parse_datetime, parse_date

from api import models
from billing.addons import AddonEntitlementService
from billing.services import BillingService
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNames, PlanNamesChoices
from observability import traced, trace
from util.analytics import Analytics
from util.tool_costs import get_most_expensive_tool_cost
from util.constants.task_constants import TASKS_UNLIMITED
from util.tool_costs import get_default_task_credit_cost
from django.db import IntegrityError, transaction
from django.conf import settings

from util.subscription_helper import (
    get_user_plan,
    get_active_subscription,
    report_task_usage_to_stripe,
    get_user_extra_task_limit,
    get_user_task_credit_limit,
    get_organization_plan,
    get_organization_extra_task_limit,
    allow_and_has_extra_tasks_for_organization,
    allow_organization_extra_tasks,
    get_organization_task_credit_limit,
)

from datetime import timedelta, datetime
from numbers import Number
from typing import Any, Mapping
from django.apps import apps
import os

import logging
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")


# Constants for task credit thresholds
THRESHOLDS = (75, 90, 100)


# NOTES:
# - "available" - refers to the number of task credits the user has that can be used immediately.
# - "granted" - refers to the number of task credits that have been granted to the user, regardless of whether
#               they are available or not
# - "entitled" - refers to the number of task credits that the user is entitled to, which may include addl_tasks that
#                are not yet granted but are available to be granted in the future.
# - "used" - refers to the number of task credits that have been used by the user.
#
# By default, all of this is "in range", that is, the task credits that are currently valid for the user, with a granted date
# in the past and an expiration date in the future. This is the most common use case.
def _extract_subscription_field(subscription: Any, key: str) -> Any:
    """Return a field from a subscription, preferring stripe_data when available."""
    if not subscription:
        return None

    source = getattr(subscription, "stripe_data", None)
    if source:
        if isinstance(source, Mapping):
            if key in source:
                return source[key]
        else:
            try:
                return getattr(source, key)
            except AttributeError:
                pass

    return getattr(subscription, key, None)


def _coerce_subscription_datetime(value: Any) -> datetime | None:
    """Convert Stripe subscription timestamp formats into datetimes."""
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value

    if isinstance(value, Number):
        try:
            return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(value, str):
        parsed_dt = parse_datetime(value.strip()) if value.strip() else None
        if parsed_dt is not None:
            return parsed_dt

        parsed_date = parse_date(value.strip()) if value.strip() else None
        if parsed_date is not None:
            return datetime.combine(parsed_date, datetime.min.time())

    return None


class TaskCreditService:
    @staticmethod
    def _is_community_unlimited() -> bool:
        """Return True when running Community Edition with unlimited credits enabled.

        Community Edition is the default (GOBII_PROPRIETARY_MODE=False). When
        GOBII_ENABLE_COMMUNITY_UNLIMITED is True (default in config/settings.py),
        all task‑credit checks should behave as unlimited to avoid low‑credit
        warnings or gating.
        """
        try:
            # Never enable unlimited mode during test runs
            if 'test_settings' in os.environ.get('DJANGO_SETTINGS_MODULE', ''):
                return False
            return (not getattr(settings, "GOBII_PROPRIETARY_MODE", False)) and bool(
                getattr(settings, "GOBII_ENABLE_COMMUNITY_UNLIMITED", False)
            )
        except Exception:
            return False
    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Calculate Available Tasks")
    def calculate_available_tasks(user, task_credits: list | None = None) -> int:
        """
        Calculates the number of task credits available for a user, including any additional tasks they may have.

        available tasks = entitled - used

        """
        # Community Edition unlimited mode: always unlimited
        if TaskCreditService._is_community_unlimited():
            return TASKS_UNLIMITED

        entitled = TaskCreditService.get_tasks_entitled(user)

        # If the user has unlimited tasks, return unlimited - we don't need to calculate anything else
        if entitled == TASKS_UNLIMITED:
            return TASKS_UNLIMITED

        used = TaskCreditService.get_user_task_credits_used(user, task_credits)

        available = entitled - used

        # This happens if the user switched plans, etc - don't allow negative available. The exception is
        # the magic value of -1 which means unlimited tasks but we already handled that above
        if available < 0:
            logger.warning(f"calculate_available_tasks: User {user.id} has more tasks used ({used}) than entitled ({entitled}). Resetting available to 0.")
            available = 0

        return available

    # -------------------- Owner-aware aggregation helpers --------------------
    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get Owner Task Credits Granted")
    def get_owner_task_credits_granted(owner, task_credits: list | None = None) -> Decimal:
        """Return total credits granted for either a User or Organization owner."""
        if TaskCreditService._is_organization_owner(owner):
            TaskCredit = apps.get_model("api", "TaskCredit")
            if task_credits is None:
                task_credits = TaskCreditService.get_current_task_credit_for_owner(owner)
            total_granted = task_credits.aggregate(total_granted=Sum("credits"))['total_granted'] or 0
            return Decimal(total_granted)
        return Decimal(TaskCreditService.get_user_task_credits_granted(owner, task_credits))

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get Owner Task Credits Used")
    def get_owner_task_credits_used(owner, task_credits: list | None = None) -> Decimal:
        """Return total credits consumed for either a User or Organization owner."""
        if TaskCreditService._is_organization_owner(owner):
            TaskCredit = apps.get_model("api", "TaskCredit")
            if task_credits is None:
                task_credits = TaskCreditService.get_current_task_credit_for_owner(owner)
            total_used = task_credits.aggregate(total_used=Sum("credits_used"))['total_used'] or 0
            return Decimal(total_used)
        return Decimal(TaskCreditService.get_user_task_credits_used(owner, task_credits))

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Calculate Available Tasks For Owner")
    def calculate_available_tasks_for_owner(owner, task_credits: list | None = None) -> Decimal:
        """Return remaining credits for a User or Organization owner."""
        if TaskCreditService._is_community_unlimited():
            return Decimal(TASKS_UNLIMITED)

        entitled = TaskCreditService.get_tasks_entitled_for_owner(owner)

        if entitled == TASKS_UNLIMITED:
            return Decimal(TASKS_UNLIMITED)

        used = TaskCreditService.get_owner_task_credits_used(owner, task_credits)
        remaining = Decimal(entitled) - Decimal(used)
        if remaining < 0:
            return Decimal(0)
        return remaining

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Grant Subscription Credits")
    def grant_subscription_credits(user, credit_override=None, plan=None, invoice_id="", grant_date=None, expiration_date=None) -> int:
        """
        Grants task credits to a user based on their subscription plan.

        This function looks up the user's subscription information, determines
        the appropriate plan and credit amount, and creates TaskCredit objects
        with the correct expiration date. If the user has no active subscription,
        they receive credits according to the free plan.

        Parameters:
        ----------
        user : User
            The user to whom the credits will be granted.

        credit_override : int, optional
            If provided, this value will override the default credit amount
            from the user's plan. This is useful for testing or special cases or grants

        plan : dict, optional
            The plan details to use for credit calculation. If not provided,
            the user's current plan will be fetched.

        invoice_id : str, optional
            The Stripe invoice ID associated with this credit grant. If provided,
            the function checks if credits for this invoice have already been granted.

        grant_date : datetime, optional
            The date when the credits are granted. If not provided, the current date and time will be used.

        expiration_date : datetime, optional
            The date when the credits expire. If not provided, it defaults to end of the users active subscription (if any),
            or 30 days from the grant date whether the grant date is provided or the current date and time.

        Returns:
        -------
        int
            The number of credits granted to the user.
        """

        # If invoice_id is provided, let's see if it's already in TaskCredit - if so, return 0 - we've handled this already
        # this can happen as stripe quickly sends multiple invoice events for the same invoice on create
        with traced("TASKCREDIT Grant Subscription Credits") as span:
            span.set_attribute('user.id', user.id)
            TaskCredit = apps.get_model("api", "TaskCredit")
            existing_credit = None

            if invoice_id:
                existing_credit = TaskCredit.objects.filter(
                    stripe_invoice_id=invoice_id,
                    voided=False
                ).first()

            subscription = None

            if credit_override is None:
                if plan is None and existing_credit is not None:
                    # Preserve legacy behavior for idempotency-only calls in tests
                    logger.debug(
                        "grant_subscription_credits %s: plan not provided and existing credit found for invoice %s; returning 0",
                        user.id,
                        invoice_id,
                    )
                    return 0

                if plan is None:
                    plan = get_user_plan(user)

                subscription = get_active_subscription(user)
                credits_to_grant = plan["monthly_task_credits"]
            else:
                credits_to_grant = credit_override

            plan_id = None
            if plan is not None:
                plan_id = getattr(plan, "id", None)
                if plan_id is None and isinstance(plan, dict):
                    plan_id = plan.get("id")
            plan_id = plan_id or PlanNamesChoices.FREE

            span.set_attribute('credits_to_grant', credits_to_grant)
            span.set_attribute('subscription.plan', plan_id)
            span.set_attribute('subscription.invoice_id', invoice_id)

            plan_choice = PlanNamesChoices(plan_id)

            grant_date = grant_date or timezone.now()

            logger.debug(f"grant_subscription_credits {user.id}: granting {credits_to_grant} credits")

            # Set expiration date - prefer the subscription's current period end
            if expiration_date is None:
                period_end = _coerce_subscription_datetime(
                    _extract_subscription_field(subscription, "current_period_end")
                ) if subscription else None

                if period_end is not None:
                    expiration_date = period_end
                else:
                    expiration_date = grant_date + timedelta(days=30)

            logger.debug(f"grant_subscription_credits {user.id}: expiration date {expiration_date}")

            if invoice_id and existing_credit:
                existing_plan = getattr(existing_credit, "plan", None)
                existing_credits = getattr(existing_credit, "credits", None)
                existing_expiration = getattr(existing_credit, "expiration_date", None)

                if existing_plan == plan_choice and existing_credits == credits_to_grant:
                    logger.debug(
                        "grant_subscription_credits %s: already granted credits for invoice %s and plan %s (same amount), returning 0",
                        user.id,
                        invoice_id,
                        plan_choice,
                    )
                    return 0

                # Plan or credit amount changed for the same invoice; update the existing credit instead of skipping.
                existing_credit.plan = plan_choice
                existing_credit.credits = credits_to_grant
                existing_credit.granted_date = grant_date
                if existing_expiration and expiration_date:
                    existing_credit.expiration_date = max(existing_expiration, expiration_date)
                else:
                    existing_credit.expiration_date = expiration_date or existing_expiration
                existing_credit.save(update_fields=["plan", "credits", "granted_date", "expiration_date"])

                logger.info(
                    "grant_subscription_credits %s: updated TaskCredit %s for invoice %s to plan %s with %s credits",
                    user.id,
                    existing_credit.id,
                    invoice_id,
                    plan_choice,
                    credits_to_grant,
                )
                return credits_to_grant

            # Create the TaskCredit for the user
            try:
                task_credit = TaskCredit.objects.create(
                    user_id=user.id,
                    credits=credits_to_grant,
                    credits_used=0,
                    expiration_date=expiration_date,
                    stripe_invoice_id=invoice_id,
                    granted_date=grant_date,
                    plan=plan_choice,
                    grant_type=GrantTypeChoices.PLAN,
                    additional_task=False,  # This is a regular task credit, not an additional task
                )
            except IntegrityError as exc:
                if plan_id == PlanNames.FREE and "uniq_free_plan_block_per_month" in str(exc):
                    logger.warning(
                        "grant_subscription_credits %s: duplicate free plan grant detected for %s; returning 0",
                        user.id,
                        grant_date,
                    )
                    return 0
                raise

            logger.debug(f"grant_subscription_credits {user.id}: created TaskCredit {task_credit.id}")

            return credits_to_grant

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Grant Org Subscription Credits")
    def grant_subscription_credits_for_organization(
        organization,
        seats: int,
        plan=None,
        invoice_id: str = "",
        grant_date=None,
        expiration_date=None,
        subscription=None,
        replace_current: bool = False,
    ) -> int:
        if seats <= 0:
            return 0

        with traced("TASKCREDIT Grant Org Subscription Credits") as span:
            TaskCredit = apps.get_model("api", "TaskCredit")

            if invoice_id:
                existing_credit = TaskCredit.objects.filter(
                    organization=organization,
                    stripe_invoice_id=invoice_id,
                    voided=False,
                ).first()
                if existing_credit:
                    logger.debug(
                        "grant_subscription_credits_for_org %s: already granted credits for invoice %s",
                        organization.id,
                        invoice_id,
                    )
                    return 0

            if plan is None:
                plan = get_organization_plan(organization)

            plan_id = plan.get("id") if plan else PlanNames.FREE

            credits_per_seat = plan.get("credits_per_seat") or plan.get("monthly_task_credits") or 0
            credits_to_grant = Decimal(credits_per_seat) * Decimal(seats)

            span.set_attribute("organization.id", str(getattr(organization, "id", "")))
            span.set_attribute("credits_to_grant", float(credits_to_grant))

            grant_date = grant_date or timezone.now()
            if timezone.is_naive(grant_date):
                grant_date = timezone.make_aware(grant_date, timezone.get_current_timezone())

            if expiration_date is None:
                period_end = _coerce_subscription_datetime(
                    _extract_subscription_field(subscription, "current_period_end")
                ) if subscription else None

                expiration_date = period_end or (grant_date + timedelta(days=30))
            elif timezone.is_naive(expiration_date):
                expiration_date = timezone.make_aware(expiration_date, timezone.get_current_timezone())

            # When replace_current is True we are handling a cycle renewal.
            # Stripe already granted the org a PLAN block for the month; the
            # renewal should refresh that block (reset usage) rather than add a
            # second copy which would double-count the entitlement.
            if replace_current:
                existing_credit = (
                    TaskCredit.objects.filter(
                        organization=organization,
                        grant_type=GrantTypeChoices.PLAN,
                        additional_task=False,
                        voided=False,
                    )
                    .filter(Q(expiration_date__isnull=True) | Q(expiration_date__gte=grant_date))
                    .order_by("-granted_date")
                    .first()
                )

                if existing_credit:
                    updates: list[str] = []
                    target_plan = PlanNamesChoices(plan_id) if plan_id else PlanNamesChoices.FREE

                    update_map = {
                        "credits": credits_to_grant,
                        "credits_used": 0,  # Renewal replenishes the block
                        "granted_date": grant_date,
                        "expiration_date": expiration_date,
                        "plan": target_plan,
                    }
                    if invoice_id:
                        update_map["stripe_invoice_id"] = invoice_id

                    for field, value in update_map.items():
                        if getattr(existing_credit, field) != value:
                            setattr(existing_credit, field, value)
                            updates.append(field)
                    if updates:
                        existing_credit.save(update_fields=updates)

                    return int(credits_to_grant)

            TaskCredit.objects.create(
                organization=organization,
                credits=credits_to_grant,
                credits_used=0,
                expiration_date=expiration_date,
                stripe_invoice_id=invoice_id or None,
                granted_date=grant_date,
                plan=PlanNamesChoices(plan_id) if plan_id else PlanNamesChoices.FREE,
                grant_type=GrantTypeChoices.PLAN,
                additional_task=False,
            )

            return int(credits_to_grant)

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Consume Credit")
    def consume_credit(user, additional_task: bool = False, amount: Decimal | None = None):
        """
        Consumes a task credit for a user. If the user has no available credits, a ValidationError is raised. Note: if
        additional_task is True, it will create a new TaskCredit with 1 credit used immediately, regardless of existing credits.

        Parameters:
        ----------
        user : User
            The user whose task credit is to be consumed.

        additional_task : bool, optional
            If True, a new TaskCredit is created with 1 credit used immediately. Defaults to False.

        Returns:
        -------
        TaskCredit
            The TaskCredit object representing the consumed credit.

        None
            if no credit was consumed (should not happen, raises ValidationError instead)

        Raises:
        -------
        ValidationError
            If the user has no available task credits or if the credit cannot be consumed.
        """
        with traced("TASKCREDIT consume_credit") as span:
            span.set_attribute('user.id', user.id)
            TaskCredit = apps.get_model("api", "TaskCredit")
            now = timezone.now()

            # Determine how many credits to consume for plan credits. For additional tasks,
            # always consume 1 (they are metered as whole tasks for billing).
            plan_amount = amount if amount is not None else get_default_task_credit_cost()

            last_credit = None
            if additional_task:
                plan = get_user_plan(user)
                with traced("TASKCREDIT Create Additional Credit", user_id=user.id) as span:
                    span.set_attribute('user.id', user.id)

                    start, end = BillingService.get_current_billing_period_for_user(user)

                    credit = TaskCredit.objects.create(
                        user_id=user.id,
                        credits=plan_amount,
                        credits_used=plan_amount,  # Consume the single additional-task credit immediately
                        expiration_date=end,
                        granted_date=start,
                        additional_task=True,
                        plan=PlanNamesChoices(plan["id"]) if plan else PlanNamesChoices.FREE,
                        grant_type=GrantTypeChoices.PLAN,
                    )
                    last_credit = credit
            else:
                # Consume possibly fractional amount across one or more credit blocks
                with transaction.atomic():
                    remaining = Decimal(plan_amount)
                    while remaining > 0:
                        with traced("TASKCREDIT Get Existing Credit") as span:
                            span.set_attribute('user.id', user.id)
                            credit = (
                                TaskCredit.objects.select_for_update()
                                .filter(
                                    user_id=user.id,
                                    expiration_date__gt=now,
                                    credits_used__lt=F("credits"),
                                    voided=False,
                                )
                                .order_by("expiration_date")
                                .first()
                            )

                        if credit is None:
                            # No more credits to consume from
                            raise ValidationError({"credits": "Insufficient task credits"})

                        # Compute available on this block using current locked row values
                        available_here = (credit.credits - credit.credits_used)

                        consume_now = available_here if available_here <= remaining else remaining
                        if consume_now <= 0:
                            # Should not happen, but defensively skip
                            break

                        credit.credits_used = F("credits_used") + consume_now
                        with traced("TASKCREDIT Save Credit"):
                            credit.save(update_fields=["credits_used"])
                        credit.refresh_from_db()
                        remaining -= consume_now
                        last_credit = credit

            # Stripe metering handled by periodic rollup task; no per-task usage reporting

            # Handle notification of task credit usage when thresholds are crossed
            TaskCreditService.handle_task_threshold(user)

            # Failsafe: if no block recorded (unlikely), fetch the next usable block for reference
            if last_credit is None and not additional_task:
                try:
                    last_credit = (
                        TaskCredit.objects
                        .filter(
                            user_id=user.id,
                            expiration_date__gt=now,
                            voided=False,
                        )
                        .order_by("expiration_date")
                        .first()
                    )
                except Exception:
                    last_credit = None

            # Return the last touched/created credit block for convenience
            return last_credit

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Tasks Entitled")
    def get_tasks_entitled(user: User) -> int:
        """Backward-compatible helper returning task entitlement for a user."""
        return TaskCreditService.get_tasks_entitled_for_owner(user)

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get Tasks Entitled For Owner")
    def get_tasks_entitled_for_owner(owner) -> int:
        """Return task entitlement for either a User or an Organization owner."""
        if TaskCreditService._is_community_unlimited():
            return TASKS_UNLIMITED

        if TaskCreditService._is_organization_owner(owner):
            return TaskCreditService._get_tasks_entitled_for_org(owner)
        return TaskCreditService._get_tasks_entitled_for_user(owner)

    @staticmethod
    def _get_tasks_entitled_for_user(user: User) -> int:
        plan = get_user_plan(user)

        if plan is None or plan["id"] == PlanNames.FREE:
            addl_tasks = 0
        else:
            addl_tasks = get_user_extra_task_limit(user)
            if addl_tasks == TASKS_UNLIMITED:
                return TASKS_UNLIMITED

        TaskCredit = apps.get_model("api", "TaskCredit")
        tasks_granted_qs = TaskCredit.objects.filter(
            user=user,
            granted_date__lte=timezone.now(),
            expiration_date__gte=timezone.now(),
            additional_task=False,
            voided=False,
        )
        tasks_granted = tasks_granted_qs.aggregate(total_granted=Sum('credits'))['total_granted'] or 0

        if tasks_granted == 0:
            monthly_limit = plan.get("monthly_task_credits") if plan else None
            addon_uplift = AddonEntitlementService.get_task_credit_uplift(user)
            tasks_granted = (monthly_limit or 0) + addon_uplift

        return tasks_granted + addl_tasks

    @staticmethod
    def _get_tasks_entitled_for_org(organization) -> int:
        billing = getattr(organization, "billing", None)
        if not billing or getattr(billing, "purchased_seats", 0) <= 0:
            return 0

        plan = get_organization_plan(organization)
        addl_limit = get_organization_extra_task_limit(organization)
        if addl_limit == TASKS_UNLIMITED:
            return TASKS_UNLIMITED

        TaskCredit = apps.get_model("api", "TaskCredit")
        tasks_granted_qs = TaskCredit.objects.filter(
            organization=organization,
            granted_date__lte=timezone.now(),
            expiration_date__gte=timezone.now(),
            additional_task=False,
            voided=False,
        )
        tasks_granted = tasks_granted_qs.aggregate(total_granted=Sum('credits'))['total_granted'] or 0

        if tasks_granted == 0:
            addon_uplift = AddonEntitlementService.get_task_credit_uplift(organization)
            tasks_granted = get_organization_task_credit_limit(organization) + addon_uplift

        if addl_limit <= 0:
            return tasks_granted
        return tasks_granted + addl_limit

    # TODO: Ripe for caching
    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Calculate Used Percentage")
    def calculate_used_pct(user: User):
        """
        Calculates the percentage of entitled task credits used by a user.

        Parameters:
        ----------
        user : User
            The user whose task credit usage is to be calculated.

        Returns:
        -------
        float
            The percentage of task credits used, or 0 if no credits are available.
        """
        total_credits = TaskCreditService.get_tasks_entitled(user)
        if total_credits == 0:
            return 0.0

        used_credits = TaskCreditService.get_user_total_tasks_used(user)
        pct = (used_credits / total_credits) * 100

        if pct > 100:
            pct = 100.0

        return pct


    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get Task Credits In Current Range")
    def get_current_task_credit(user):
        """
        Gets the set of task credits that are currently valid for a user. That is, the task credits that have been granted
        and are not expired. This is useful for determining how many task credits a user has available to use at the moment,
        and other aggregations like how many task credits have been used in the current subscription period.
        """
        span = trace.get_current_span()
        try:
            TaskCredit = apps.get_model("api", "TaskCredit")
            task_credits = TaskCredit.objects.filter(
                user=user,
                granted_date__lte=timezone.now(),
                # Ensure the granted date is in the past, that is, the credits have been granted
                expiration_date__gte=timezone.now(),
                voided=False,  # Ensure we don't include voided credits
            ).order_by('-expiration_date')

            span.set_attribute('task_credits.count', task_credits.count())
        except Exception as e:
            logger.error(f"get_task_credits_in_current_range: Error fetching task credits for user {user.id}: {str(e)}")
            return TaskCredit.objects.none()

        return task_credits

    # -------------------- Owner-aware APIs (User or Organization) --------------------
    @staticmethod
    def _is_organization_owner(owner) -> bool:
        meta = getattr(owner, "_meta", None)
        return bool(meta and meta.app_label == "api" and meta.model_name == "organization")

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get Current Credit For Owner")
    def get_current_task_credit_for_owner(owner):
        """
        Returns TaskCredit queryset for an owner, which may be a User or an Organization.
        """
        TaskCredit = apps.get_model("api", "TaskCredit")
        now = timezone.now()
        if TaskCreditService._is_organization_owner(owner):
            return TaskCredit.objects.filter(
                organization=owner,
                granted_date__lte=now,
                expiration_date__gte=now,
                voided=False,
            ).order_by('-expiration_date')
        else:
            return TaskCreditService.get_current_task_credit(owner)

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Consume Credit For Owner")
    def consume_credit_for_owner(owner, additional_task: bool = False, amount: Decimal | None = None):
        """
        Consume a credit for either a User or an Organization.
        For organizations, additional_task credits are created with a 30-day expiry window for now.
        """
        TaskCredit = apps.get_model("api", "TaskCredit")
        now = timezone.now()
        plan_amount = Decimal(amount if amount is not None else get_default_task_credit_cost())

        if TaskCreditService._is_organization_owner(owner):
            if additional_task:
                plan = get_organization_plan(owner)
                period_start, period_end = BillingService.get_current_billing_period_for_owner(owner)
                credit = TaskCredit.objects.create(
                    organization_id=owner.id,
                    credits=plan_amount,
                    credits_used=plan_amount,
                    expiration_date=period_end,
                    granted_date=period_start,
                    additional_task=True,
                    plan=PlanNamesChoices(plan["id"]) if plan else PlanNamesChoices.FREE,
                    grant_type=GrantTypeChoices.PLAN,
                )
                return credit
            else:
                # Fractional consumption for organizations across blocks
                with transaction.atomic():
                    remaining = Decimal(plan_amount)
                    last_credit = None
                    while remaining > 0:
                        credit = (
                            TaskCredit.objects.select_for_update()
                            .filter(
                                organization_id=owner.id,
                                expiration_date__gt=now,
                                credits_used__lt=F("credits"),
                                voided=False,
                            )
                            .order_by("expiration_date")
                            .first()
                        )

                        if credit is None:
                            raise ValidationError({"credits": "Insufficient task credits for organization"})

                        credit.refresh_from_db()
                        available_here = (credit.credits - credit.credits_used)
                        consume_now = available_here if available_here <= remaining else remaining
                        if consume_now <= 0:
                            break

                        credit.credits_used = F("credits_used") + consume_now
                        credit.save(update_fields=["credits_used"])
                        credit.refresh_from_db()
                        remaining -= consume_now
                        last_credit = credit

                return last_credit
        else:
            # Assume user
            return TaskCreditService.consume_credit(owner, additional_task=additional_task, amount=plan_amount)

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Check And Consume Credit For Owner")
    def check_and_consume_credit_for_owner(owner, amount: Decimal | None = None) -> dict:
        """Owner-aware wrapper mirroring check_and_consume_credit."""
        from django.core.exceptions import ValidationError

        if TaskCreditService._is_organization_owner(owner):
            try:
                credit = TaskCreditService.consume_credit_for_owner(owner, amount=amount)
                return {"success": True, "credit": credit, "error_message": None}
            except ValidationError:
                if allow_and_has_extra_tasks_for_organization(owner):
                    try:
                        credit = TaskCreditService.consume_credit_for_owner(
                            owner,
                            additional_task=True,
                            amount=amount,
                        )
                        return {"success": True, "credit": credit, "error_message": None}
                    except ValidationError:
                        pass

                error_message = (
                    "Organization has no remaining task credits nor additional tasks allowed."
                    if allow_organization_extra_tasks(owner)
                    else "Organization has no remaining task credits."
                )
                return {
                    "success": False,
                    "credit": None,
                    "error_message": error_message,
                }
        else:
            return TaskCreditService.check_and_consume_credit(owner, amount=amount)

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Tasks Credits Available")
    def get_user_task_credits_available(user, task_credits: list | None = None) -> int:
        """
        Gets the number of task credits available for a user.

        This function retrieves the user's task credit limit based on their TaskCredit grants and
        calculates the available task credits, which includes one-off grants, addl tasks, etc. Not simply the
        monthly task credit limit.

        Parameters:
            user (User): The user for whom the available task credits are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The number of task credits available for the user.
        """
        # Community Edition unlimited mode
        if TaskCreditService._is_community_unlimited():
            return TASKS_UNLIMITED

        TaskCredit = apps.get_model("api", "TaskCredit")
        entitled = TaskCreditService.get_tasks_entitled(user)

        if entitled == TASKS_UNLIMITED:
            # If the user has unlimited tasks, return unlimited
            return TASKS_UNLIMITED

        if task_credits is None:
            # Fetch the task credits for the user in the current range
            task_credits = TaskCreditService.get_current_task_credit(user)

        used = TaskCreditService.get_user_task_credits_used(user, task_credits)

        return entitled - used

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Task Credits Used")
    def get_user_task_credits_used(user, task_credits: list | None = None) -> int:
        """
        Gets the number of task credits used by a user.

        This function retrieves the user's task credit usage based on their TaskCredit grants and
        calculates the total task credits used. It does include addl overage

        Parameters:
            user (User): The user for whom the task credits used are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The number of task credits used by the user.
        """
        if task_credits is None:
            # Fetch the task credits for the user in the current range
            task_credits = TaskCreditService.get_current_task_credit(user)

        total_used = task_credits.aggregate(total_used=Sum('credits_used'))['total_used'] or 0

        return total_used

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Task Credits Granted")
    def get_user_task_credits_granted(user, task_credits: list | None = None) -> int:
        """
        Gets the number of task credits granted to a user.

        This function retrieves the user's task credit grants based on their TaskCredit entries
        and calculates the total task credits granted. It does not include addl tasks

        Parameters:
            user (User): The user for whom the task credits granted are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The number of task credits granted to the user.
        """
        if task_credits is None:
            # Fetch the task credits for the user in the current range
            task_credits = TaskCreditService.get_current_task_credit(user)

        total_granted = task_credits.aggregate(total_granted=Sum('credits'))['total_granted'] or 0

        return total_granted

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Task Credits Used Percentage")
    def get_user_task_credits_used_pct(user, task_credits: list | None = None) -> float:
        """
        Gets the percentage of task credits used by a user.

        This function calculates the percentage of task credits used based on the total entitled, including addl tasks.

        Parameters:
            user (User): The user for whom the task credits used percentage is being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            float: The percentage of task credits used by the user.
        """
        # Community Edition unlimited mode
        if TaskCreditService._is_community_unlimited():
            return 0.0

        total_entitled = TaskCreditService.get_tasks_entitled(user)

        if total_entitled == TASKS_UNLIMITED:
            return 0.0

        total_used = TaskCreditService.get_user_task_credits_used(user, task_credits)

        try:
            pct = (total_used / total_entitled) * 100
        except ZeroDivisionError:
            logger.warning(f"get_user_task_credits_used_pct: User {user.id} has no task credits entitled, returning 0%")
            pct = 0.0

        if pct > 100:
            pct = 100.0

        return pct


    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Additional Tasks Used")
    def get_user_additional_tasks_used(user: User, task_credits: list | None = None) -> int:
        """
        Gets the number of additional tasks used by a user (sum of credits_used).

        Currently, additional-task credits are granted as 1.0 units per event, so this
        is equivalent to counting events. Summing credits_used keeps behavior robust if
        the per-event unit changes in the future.

        Parameters:
            user (User): The user for whom the additional tasks available are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The number of additional tasks available for the user.
        """

        if task_credits is None:
            task_credits = TaskCreditService.get_current_task_credit(user)

        # Sum the credits_used across additional-task blocks in the current window
        total_used = (
            task_credits
            .filter(additional_task=True, voided=False)
            .aggregate(total_used=Sum('credits_used'))['total_used'] or 0
        )
        return int(total_used)

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Additional Tasks Available")
    def get_user_additional_tasks_available(user: User, task_credits: list | None = None) -> int:
        """
        Gets the number of additional tasks available for a user.

        This function retrieves the user's additional task limit based on their subscription plan
        and calculates the available additional tasks.

        Parameters:
            user (User): The user for whom the additional tasks available are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The number of additional tasks available for the user.
        """
        limit = get_user_extra_task_limit(user)

        if limit == TASKS_UNLIMITED:
            # If the user has unlimited additional tasks, return unlimited
            return TASKS_UNLIMITED

        addl_used = TaskCreditService.get_user_additional_tasks_used(user, task_credits)
        limit -= addl_used

        if limit < 0:
            logger.warning(f"get_user_additional_tasks_available: User {user.id} has more additional tasks used ({addl_used}) than limit ({limit}). Resetting to 0.")
            limit = 0

        return limit

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Additional Tasks Used Percentage")
    def get_user_additional_tasks_used_pct(user: User, task_credits: list | None = None) -> float:
        """
        Gets the percentage of additional tasks used by a user.

        This function calculates the percentage of additional tasks used based on the total additional tasks available
        and the additional tasks used.

        Parameters:
            user (User): The user for whom the additional tasks used percentage is being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            float: The percentage of additional tasks used by the user.
        """
        total_addl = get_user_extra_task_limit(user)

        if total_addl == 0 or total_addl == TASKS_UNLIMITED:
            return 0.0

        total_used = TaskCreditService.get_user_additional_tasks_used(user, task_credits)

        pct = (total_used / total_addl) * 100

        if pct > 100:
            pct = 100.0

        return pct

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Total Tasks Available")
    def get_user_total_tasks_available(user: User, task_credits: list | None = None) -> int:
        """
        Gets the total number of tasks available for a user, including both regular and additional tasks. This the
        entitled tasks available to the user, which includes the monthly task credits, promos, and any additional tasks
        they may have enabled. Is addl_tasks is TASKS_UNLIMITED, then the user has unlimited tasks available.

        Parameters:
            user (User): The user for whom the total tasks available are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The total number of tasks available for the user.
        """
        additional_tasks = TaskCreditService.get_user_additional_tasks_available(user, task_credits)

        if additional_tasks == TASKS_UNLIMITED:
            # If the user has unlimited additional tasks, return unlimited
            return TASKS_UNLIMITED


        total_tasks = TaskCreditService.get_tasks_entitled(user) - TaskCreditService.get_user_task_credits_used(user, task_credits)

        return total_tasks

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Total Tasks Used")
    def get_user_total_tasks_used(user: User, task_credits: list | None = None) -> int:
        """
        Gets the total number of tasks used by a user, including both regular and additional tasks.

        Parameters:
            user (User): The user for whom the total tasks used are being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            int: The total number of tasks used by the user.
        """
        if task_credits is None:
            task_credits = TaskCreditService.get_current_task_credit(user)

        total_used = task_credits.aggregate(total_used=Sum('credits_used'))['total_used'] or 0

        return total_used

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Get User Total Tasks Used Percentage")
    def get_user_total_tasks_used_pct(user: User, task_credits: list | None = None) -> float:
        """
        Gets the percentage of total tasks used by a user, including both regular and additional tasks.

        Parameters:
            user (User): The user for whom the total tasks used percentage is being calculated.
            task_credits (list, optional): A list of TaskCredit objects. If not provided, it will fetch the task credits.
                                           Provide to prevent multiple database queries if you already have the task credits.

        Returns:
            float: The percentage of total tasks used by the user.
        """
        # Community Edition unlimited mode
        if TaskCreditService._is_community_unlimited():
            return 0.0

        total_available = TaskCreditService.get_tasks_entitled(user)

        if total_available == TASKS_UNLIMITED:
            return 0.0

        total_used = TaskCreditService.get_user_total_tasks_used(user, task_credits)

        pct = (total_used / total_available) * 100

        if pct > 100:
            pct = 100.0

        return pct

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Handle Task Threshold")
    def handle_task_threshold(user):
        """
        Handles task usage thresholds for a user. This function updates the user's monthly task usage counter and checks
        if any thresholds have been crossed. If a threshold is crossed, it publishes an event to notify the system.
        """
        now = timezone.now()
        period_ym = now.strftime("%Y%m")  # e.g. '202507'

        with transaction.atomic():
            entitled = TaskCreditService.get_tasks_entitled(user)
            used = TaskCreditService.get_user_total_tasks_used(user)

            if entitled == TASKS_UNLIMITED or entitled == 0:
                pct = 0.0
            else:
                entitled_decimal = Decimal(entitled)
                used_decimal = Decimal(used)

                pct_decimal = (used_decimal / entitled_decimal) * Decimal(100)

                if pct_decimal > Decimal(100):
                    pct_decimal = Decimal(100)
                else:
                    tolerance = get_most_expensive_tool_cost()
                    tolerance = max(tolerance, Decimal("0.001"))

                    remaining = entitled_decimal - used_decimal
                    if remaining <= tolerance:
                        pct_decimal = Decimal(100)

                pct = float(pct_decimal)

            UsageThresholdSent = apps.get_model("api", "UsageThresholdSent")

            # 2️⃣Fire at most ONE threshold per call
            for t in THRESHOLDS:
                if pct >= t:
                    sent_row, created = UsageThresholdSent.objects.get_or_create(
                        user=user,
                        period_ym=period_ym,
                        threshold=t,
                        defaults={"plan_limit": entitled},
                    )
                    if created:  # this threshold hasn't fired this month
                        Analytics.publish_threshold_event(user.id, t, int(pct), period_ym, used, entitled)
                        break  # stop at the *lowest* new threshold

    @staticmethod
    @tracer.start_as_current_span("TaskCreditService Check And Consume Credit")
    def check_and_consume_credit(user, amount: Decimal | None = None) -> dict:
        """
        Atomically attempts to consume a task credit for ``user``.

        The previous implementation counted credits first (without locking) and
        then attempted to consume one, which opened a race window under heavy
        parallelism. The new approach is:

        1. Try to consume a regular credit using ``consume_credit``. That helper
           already performs a ``SELECT … FOR UPDATE`` on the candidate row and
           uses an F-expression increment – this is the only safe atomic step
           we need.
        2. If that fails with ``ValidationError`` (no regular credits left),
           attempt to consume an "additional task" credit for paid plans when
           allowed.
        3. Otherwise, return an error result.
        """
        # Local imports to avoid circular dependencies
        from django.core.exceptions import ValidationError
        from util.subscription_helper import get_active_subscription, allow_and_has_extra_tasks

        # Community Edition unlimited mode: always succeed without consuming
        if TaskCreditService._is_community_unlimited():
            return {
                "success": True,
                "credit": None,
                "error_message": None,
            }

        with tracer.start_as_current_span("CHECK User TaskCredit") as span:
            span.set_attribute("user.id", str(user.id))

            # --- 1. Optimistic consume of regular credit (atomic) ---
            try:
                credit = TaskCreditService.consume_credit(user, amount=amount)
                span.add_event("Consumed regular task credit")
                return {
                    "success": True,
                    "credit": credit,
                    "error_message": None,
                }
            except ValidationError:
                span.add_event("No regular credits – trying additional task path")

            # --- 2. Attempt additional-task credit for paid plans ---
            subscription = get_active_subscription(user)
            if subscription is not None and allow_and_has_extra_tasks(user):
                try:
                    credit = TaskCreditService.consume_credit(user, additional_task=True)
                    span.add_event("Consumed additional task credit")
                    return {
                        "success": True,
                        "credit": credit,
                        "error_message": None,
                    }
                except ValidationError:
                    # Highly unlikely – another race between the extra-task count
                    span.add_event("Additional task credit creation failed")

            # --- 3. Out of credits ---
            span.add_event("Insufficient credits – quota exceeded")
            return {
                "success": False,
                "credit": None,
                "error_message": "Task quota exceeded. You have no remaining task credits and no active subscription." if subscription is None else "Task quota exceeded. You have no remaining task credits nor additional tasks allowed.",
            }
