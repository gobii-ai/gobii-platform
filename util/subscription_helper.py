from django.contrib.auth import get_user_model
from django.db.models.expressions import OuterRef, Subquery
from django.db.models.query_utils import Q
from django.db.utils import IntegrityError
from djstripe.models import Customer

from constants.grant_types import GrantTypeChoices
from config.plans import PLAN_CONFIG, get_plan_by_product_id, AGENTS_UNLIMITED
from config.stripe_config import get_stripe_settings
from constants.plans import PlanNames
from datetime import datetime, timedelta, date, time
from django.utils import timezone
import logging
from typing import Literal, Tuple, Any

from observability import traced, trace
from util.constants.task_constants import TASKS_UNLIMITED
from util.payments_helper import PaymentsHelper
from djstripe.enums import SubscriptionStatus
from django.apps import apps
from dateutil.relativedelta import relativedelta

try:
    import stripe
    from djstripe.models import Subscription

    DJSTRIPE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    stripe = None  # type: ignore
    Subscription = None  # type: ignore
    DJSTRIPE_AVAILABLE = False

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("gobii.utils")

BillingOwnerType = Literal["user", "organization"]


def _resolve_owner_type(owner: Any) -> BillingOwnerType:
    """Return whether the provided owner is a user or organization."""
    if owner is None:
        raise ValueError("Owner instance is required")

    UserModel = get_user_model()
    Organization = apps.get_model("api", "Organization")

    if isinstance(owner, UserModel):
        return "user"
    if isinstance(owner, Organization):
        return "organization"

    raise TypeError(f"Unsupported owner type: {owner.__class__.__name__}")


def _get_billing_model_and_filters(owner: Any) -> Tuple[Any, dict[str, Any], BillingOwnerType]:
    """Return the billing model, filter kwargs, and owner type for the owner."""
    owner_type = _resolve_owner_type(owner)

    if owner_type == "user":
        BillingModel = apps.get_model("api", "UserBilling")
        filters = {"user": owner}
    else:
        BillingModel = apps.get_model("api", "OrganizationBilling")
        filters = {"organization": owner}

    return BillingModel, filters, owner_type


def _get_billing_record(owner: Any):
    BillingModel, filters, _ = _get_billing_model_and_filters(owner)
    return BillingModel.objects.filter(**filters).first()


def _get_or_create_billing_record(owner: Any, defaults: dict[str, Any] | None = None):
    BillingModel, filters, owner_type = _get_billing_model_and_filters(owner)
    defaults = defaults.copy() if defaults else {}

    if owner_type == "organization" and "billing_cycle_anchor" not in defaults:
        defaults.setdefault("billing_cycle_anchor", timezone.now().day)

    return BillingModel.objects.get_or_create(**filters, defaults=defaults)

def get_stripe_customer(owner) -> Customer | None:
    """Return the Stripe customer associated with a user or organization owner."""
    with traced("SUBSCRIPTION - Get Stripe Customer"):
        owner_type = _resolve_owner_type(owner)

        if owner_type == "user":
            try:
                return Customer.objects.get(subscriber=owner)
            except Customer.DoesNotExist:
                return None

        billing = _get_billing_record(owner)
        if not billing or not getattr(billing, "stripe_customer_id", None):
            return None

        try:
            return Customer.objects.get(id=billing.stripe_customer_id)
        except Customer.DoesNotExist:
            logger.warning(
                "Stripe customer %s referenced by organization %s is missing locally",
                billing.stripe_customer_id,
                getattr(owner, "id", "unknown"),
            )
            return None

def get_active_subscription(owner) -> Subscription | None:
    """Fetch the first active licensed subscription for a user or organization."""
    with traced("SUBSCRIPTION - Get Active Subscription") as span:
        owner_type = _resolve_owner_type(owner)
        owner_id = getattr(owner, "id", None) or getattr(owner, "pk", None)
        span.set_attribute("owner.type", owner_type)
        if owner_id is not None:
            span.set_attribute("owner.id", str(owner_id))

        customer = get_stripe_customer(owner)
        logger.debug("get_active_subscription %s %s: %s", owner_type, owner_id, customer)

        if not customer:
            span.set_attribute("owner.customer", "")
            return None

        now_ts = int(timezone.now().timestamp())

        # Statuses you consider “active” for licensing (tweak as needed)
        ACTIVE_STATUSES = ["active", "trialing"]  # add "past_due" if you still grant access

        qs = customer.subscriptions.filter(
            stripe_data__status__in=ACTIVE_STATUSES,
            stripe_data__current_period_end__gte=now_ts,
        )

        # If you want the one that ends soonest, prefer ordering in Python (simplest & portable):
        subs = list(qs)
        subs.sort(key=lambda s: s.stripe_data.get("cancel_at_period_end") or 0)

        span.set_attribute("owner.customer.id", str(customer.id))
        logger.debug(
            "get_active_subscription %s %s subscriptions: %s",
            owner_type,
            owner_id,
            subs,
        )

        return subs[0] if subs else None

def user_has_active_subscription(user) -> bool:
    """
    Checks whether the specified user has an active subscription.

    This function determines if the given user has an active subscription
    based on the result of the `get_active_subscription` function.

    Args:
        user: The user object for which the active subscription status
        is being checked.

    Returns:
        bool: True if the user has an active subscription, otherwise False.
    """
    return get_active_subscription(user) is not None

def get_owner_plan(owner) -> dict[str, int | str]:
    """Return plan configuration for a user or organization owner."""
    with traced("SUBSCRIPTION Get Owner Plan"):
        owner_type = _resolve_owner_type(owner)
        owner_id = getattr(owner, "id", None) or getattr(owner, "pk", None)

        subscription = get_active_subscription(owner)
        logger.debug("get_owner_plan %s %s: %s", owner_type, owner_id, subscription)

        if not subscription:
            logger.debug("get_owner_plan %s %s: No active subscription found", owner_type, owner_id)
            return PLAN_CONFIG[PlanNames.FREE]

        stripe_sub = subscription.stripe_data

        product_id = None
        for item_data in stripe_sub.get("items", {}).get("data", []):
            if item_data.get("plan", {}).get("usage_type") == "licensed":
                product_id = item_data.get("price", {}).get("product")
                break

        logger.debug("get_owner_plan %s %s product_id: %s", owner_type, owner_id, product_id)

        if not product_id:
            logger.warning("get_owner_plan %s %s: Subscription product is None", owner_type, owner_id)
            return PLAN_CONFIG[PlanNames.FREE]

        plan = get_plan_by_product_id(product_id)
        return plan if plan else PLAN_CONFIG[PlanNames.FREE]


def get_user_plan(user) -> dict[str, int | str]:
    return get_owner_plan(user)


def get_organization_plan(organization) -> dict[str, int | str]:
    return get_owner_plan(organization)

def get_user_task_credit_limit(user) -> int:
    """
    Gets the monthly task credit limit for a user's plan.

    This function retrieves the plan associated with a user and determines the
    monthly task credit limit based on the plan. If the user does not have an
    associated plan, it defaults to the free plan's task credit limit.

    Parameters:
        user (User): The user for whom the task credit limit is being fetched.

    Returns:
        int: The monthly task credit limit for the user's plan.

    Raises:
        None
    """
    with traced("CREDITS Get User Task Credit Limit") as span:
        span.set_attribute("user.id", user.id)
        plan = get_user_plan(user)

        if not plan:
            logger.warning(f"get_user_task_credit_limit {user.id}: No plan found, defaulting to free plan")
            return PLAN_CONFIG[PlanNames.FREE]["monthly_task_credits"]

        return plan["monthly_task_credits"]

def get_or_create_stripe_customer(owner) -> Customer:
    """Return an existing Stripe customer for the owner or create a new one."""
    with traced("SUBSCRIPTION Get or Create Stripe Customer"):
        stripe_key = PaymentsHelper.get_stripe_key()
        stripe.api_key = stripe_key

        owner_type = _resolve_owner_type(owner)

        if owner_type == "user":
            customer = Customer.objects.filter(subscriber=owner).first()
            billing = None
        else:
            billing, _ = _get_or_create_billing_record(owner)
            customer = None
            if billing.stripe_customer_id:
                customer = Customer.objects.filter(id=billing.stripe_customer_id).first()

        if customer:
            return customer

        metadata: dict[str, Any] = {"owner_type": owner_type}

        if owner_type == "user":
            email = getattr(owner, "email", None)
            name = getattr(owner, "get_full_name", lambda: None)() or getattr(owner, "username", None)
            metadata["user_id"] = owner.pk
        else:
            email = getattr(owner, "billing_email", None)
            if not email:
                creator = getattr(owner, "created_by", None)
                email = getattr(creator, "email", None)
            name = getattr(owner, "name", None)
            metadata["organization_id"] = str(owner.pk)

        with traced("STRIPE Create Customer"):
            stripe_customer = stripe.Customer.create(
                email=email,
                name=name,
                metadata={k: v for k, v in metadata.items() if v is not None},
                api_key=stripe_key,
            )

        customer = Customer.sync_from_stripe_data(stripe_customer)

        if owner_type == "user":
            customer.subscriber = owner
            customer.save(update_fields=["subscriber"])
        else:
            # Persist the Stripe ID on the organization billing record for quick lookups
            billing.stripe_customer_id = customer.id
            billing.save(update_fields=["stripe_customer_id"])

        return customer

def get_user_api_rate_limit(user) -> int:
    """
    Determines the API rate limit for a given user based on their subscription
    plan. If the user does not have an associated plan, defaults to the rate limit
    defined for the free plan. Logs a warning when no plan is found for a user.

    Parameters:
    user (User): The user object for whom the API rate limit is being retrieved.

    Returns:
    int: The API rate limit associated with the user's plan, or the default
    rate limit for the free plan if no plan is found.
    """
    with traced("SUBSCRIPTION Get User API Rate Limit"):
        plan = get_user_plan(user)

        if not plan:
            logger.warning(f"get_user_api_rate_limit {user.id}: No plan found, defaulting to free plan")
            return PLAN_CONFIG[PlanNames.FREE]["api_rate_limit"]

        return plan["api_rate_limit"]

def get_user_agent_limit(user) -> int:
    """
    Determines the user agent limit based on their subscribed plan. If the user does
    not have a valid plan, it defaults to the free plan limit.

    Args:
        user: The user object for which the agent limit is to be determined.

    Returns:
    int
        An integer indicating the maximum number of agents the user is allowed to
        utilize.
    """
    with traced("SUBSCRIPTION Get User Agent Limit"):
        plan = get_user_plan(user)

        if not plan:
            logger.warning(f"get_user_agent_limit {user.id}: No plan found, defaulting to free plan")
            return PLAN_CONFIG[PlanNames.FREE]["agent_limit"]

        return plan["agent_limit"]

def report_task_usage_to_stripe(user, quantity: int = 1, meter_id: str | None = None, idempotency_key: str | None = None):
    """
    Reports usage to Stripe by creating a UsageRecord.

    This function checks if the user has an active subscription and a Stripe customer ID.
    If both conditions are met, it creates a UsageRecord in Stripe for the specified
    quantity of usage against the given meter ID.

    Parameters:
    ----------
    user : User | int
        The user for whom the usage is being reported.
    quantity : int, optional
        The quantity of usage to report (default is 1).
    meter_id : str, optional
        The ID of the meter to report usage against. If not provided,
        defaults to the configured task meter in StripeConfig/environment.

    Returns:
    -------
    UsageRecord or None
        The created UsageRecord if successful, None if no reporting was done
        (due to free tier or missing customer).
    """

    # If user is an id (int) instead of a User object, fetch the user
    with traced("SUBSCRIPTION Report Task Usage to Stripe"):
        if isinstance(user, int):
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user)

        # Skip if user doesn't have an active subscription (free tier)
        subscription = get_active_subscription(user)
        if not subscription:
            logger.debug(f"report_usage_to_stripe: User {user.id} has no active subscription, skipping")
            return None

        # Get the Stripe customer for this user
        customer = get_stripe_customer(user)
        if not customer:
            logger.debug(f"report_usage_to_stripe: User {user.id} has no Stripe customer, skipping")
            return None

        stripe_settings = get_stripe_settings()

        # se default meter ID if meter_id is not provided or is falsy (e.g., None or empty string).
        if not meter_id:
            meter_id = stripe_settings.task_meter_id

        # Create the usage record in Stripe
        try:
            logger.debug(
                f"report_usage_to_stripe: Reporting {quantity} usage for user {user.id} on meter {meter_id}"
            )

            stripe.api_key = PaymentsHelper.get_stripe_key()
            # Only pass idempotency_key when present to keep backward-compat with tests
            if idempotency_key is not None:
                return report_task_usage(subscription, quantity=quantity, idempotency_key=idempotency_key)
            else:
                # Maintain legacy behavior for callers/tests: do not return a record
                report_task_usage(subscription, quantity=quantity)
                return None

            # usage_record = UsageRecord.create(
            #     subscription_item=customer.subscription_items.get(
            #         price__metered=True, price__lookup_key=meter_id
            #     ),
            #     quantity=quantity,
            #     timestamp=None,  # Use current time
            #     action="increment",
            # )
            # return usage_record

        except Exception as e:
            logger.error(f"report_usage_to_stripe: Error reporting usage for user {user.id}: {str(e)}")
            raise


def report_organization_task_usage_to_stripe(organization, quantity: int = 1,
                                             meter_id: str | None = None,
                                             idempotency_key: str | None = None):
    """Report additional task usage for an organization via Stripe metering."""
    with traced("SUBSCRIPTION Report Org Task Usage"):
        billing = getattr(organization, "billing", None)
        if not billing or not getattr(billing, "stripe_customer_id", None):
            logger.debug(
                "report_org_usage_to_stripe: Organization %s missing Stripe customer, skipping",
                getattr(organization, "id", "n/a"),
            )
            return None

        # TODO: Overhaul this to use the properties we will define for orgs and their tasks (since org plans have their own
        # task meters)
        stripe_settings = get_stripe_settings()

        if not meter_id:
            meter_id = stripe_settings.org_task_meter_id or stripe_settings.task_meter_id

        try:
            stripe.api_key = PaymentsHelper.get_stripe_key()
            meter_event = stripe.billing.MeterEvent.create(
                event_name=stripe_settings.task_meter_event_name,
                payload={"value": quantity, "stripe_customer_id": billing.stripe_customer_id},
                idempotency_key=idempotency_key,
            )
            return meter_event
        except Exception as e:
            logger.error(
                "report_org_usage_to_stripe: Error reporting usage for organization %s: %s",
                getattr(organization, "id", "n/a"),
                str(e),
            )
            raise


def report_task_usage(subscription: Subscription, quantity: int = 1, idempotency_key: str | None = None):
    """
    Report task usage to Stripe for a given subscription.

    This function is called when a user has an active subscription; Free subscribers do not have an active subscription
    and therefore do not report usage. It creates a MeterEvent in Stripe to report the usage of tasks.

    Args:
        subscription (Subscription): The active subscription object.
        quantity (int): The number of extra tasks to report. Defaults to 1.
    """
    with traced("SUBSCRIPTION Report Task Usage"):
        if not DJSTRIPE_AVAILABLE or not subscription:
            return
        try:
            stripe.api_key = PaymentsHelper.get_stripe_key()
            stripe_settings = get_stripe_settings()

            with traced("STRIPE Create Meter Event"):
                meter_event = stripe.billing.MeterEvent.create(
                    event_name=stripe_settings.task_meter_event_name,
                    payload={"value": quantity, "stripe_customer_id": subscription.customer.id},
                    idempotency_key=idempotency_key,
                )
                return meter_event

        except Exception as e:
            logger.error(f"report_task_usage: Error reporting task usage: {str(e)}")
            raise

def get_free_plan_users():
    """
    Retrieves all users who are currently on the free plan.

    This function queries the database for all users whose associated plan is
    the free plan. It returns a list of user objects.

    Returns:
    -------
    list[User]
        A list of user objects who are subscribed to the free plan.
    """
    from django.contrib.auth import get_user_model
    with traced("SUBSCRIPTION Get Free Plan Users"):
        users = get_user_model()

        active_subscriber_ids = (
            Subscription.objects
            .filter(status=SubscriptionStatus.active)  # or .in_(["active", "trialing"])
            .values_list("customer__subscriber_id", flat=True)  # FK hop: Subscription ➜ Customer ➜ subscriber (User)
        )

        users_without_active_sub = users.objects.exclude(id__in=active_subscriber_ids)

        return users_without_active_sub

def get_users_due_for_monthly_grant(days=1):
    """
    Retrieves users who are due for their free monthly task credit grant.

    This function identifies users who have not received a 'Plan' type grant
    in the last 30 days, or who have never received one. This is used to
    trigger the monthly grant for free-tier users.

    Parameters:
    ----------
    days : int, optional
        The number of days within which to check for expiring task credits (default is 7).

    Returns:
    -------
    list[User]
        A list of user objects whose task credits are expiring soon.
    """
    with traced("CREDITS Get Users with Credits Expiring Soon"):
        # Subquery to get each user's latest granted date
        TaskCredit = apps.get_model("api", "TaskCredit")
        latest_grant = (
            TaskCredit.objects
            .filter(user=OuterRef('pk'), grant_type=GrantTypeChoices.PLAN, voided=False)
            .order_by('-granted_date')
            .values('granted_date')[:1]
        )

        # Users whose last grant was 30+ days ago, or who have no grant history
        User = get_user_model()
        users_to_grant = User.objects.annotate(
            last_grant_date=Subquery(latest_grant)
        ).filter(
            Q(last_grant_date__lte=date.today() - timedelta(days=30)) |
            Q(last_grant_date__isnull=True)
        )

        return users_to_grant

# Take a list of users, and return only the ones without an active subscription
def filter_users_without_active_subscription(users):
    """
    Filters a list of users to return only those without an active subscription.

    This function checks each user in the provided list and returns a new list
    containing only those users who do not have an active subscription.

    Parameters:
    ----------
    users : list[User]
        A list of user objects to be filtered.

    Returns:
    -------
    list[User]
        A list of user objects that do not have an active subscription.
    """
    with traced("SUBSCRIPTION Filter Users Without Active Subscription"):
        return [user for user in users if not get_active_subscription(user)]

def mark_owner_billing_with_plan(owner, plan_name: str, update_anchor: bool = True):
    """Persist the selected plan on the owner billing record (user or organization)."""
    with traced("SUBSCRIPTION Mark Billing with Plan") as span:
        owner_type = _resolve_owner_type(owner)
        owner_id = getattr(owner, "id", None) or getattr(owner, "pk", None)
        span.set_attribute("owner.type", owner_type)
        if owner_id is not None:
            span.set_attribute("owner.id", str(owner_id))
        span.set_attribute("update_anchor", str(update_anchor))

        defaults = {"subscription": plan_name}
        if update_anchor:
            defaults["billing_cycle_anchor"] = timezone.now().day

        billing_record, created = _get_or_create_billing_record(owner, defaults=defaults)
        prev_plan = None if created else billing_record.subscription

        updates: list[str] = []
        if created:
            return billing_record

        for key, value in defaults.items():
            if getattr(billing_record, key) != value:
                setattr(billing_record, key, value)
                updates.append(key)

        if prev_plan and prev_plan != PlanNames.FREE and plan_name == PlanNames.FREE:
            billing_record.downgraded_at = timezone.now()
            updates.append("downgraded_at")
        elif plan_name != PlanNames.FREE and getattr(billing_record, "downgraded_at", None):
            billing_record.downgraded_at = None
            updates.append("downgraded_at")

        if updates:
            billing_record.save(update_fields=updates)

        span.add_event(
            "Subscription - Updated",
            {
                "owner.type": owner_type,
                "owner.id": str(owner_id) if owner_id is not None else "",
                "plan.name": plan_name,
            },
        )

        if owner_type == "user" and plan_name != PlanNames.FREE:
            try:
                from api.models import PersistentAgent

                agents = (
                    PersistentAgent.objects
                    .filter(user=owner, life_state=PersistentAgent.LifeState.EXPIRED)
                    .exclude(schedule__isnull=True)
                    .exclude(schedule="")
                )
                for agent in agents:
                    # Mark active and recreate beat entry
                    agent.life_state = PersistentAgent.LifeState.ACTIVE
                    agent.save(update_fields=["life_state"])
                    from django.db import transaction

                    transaction.on_commit(agent._sync_celery_beat_task)
            except Exception as e:
                logger.error(
                    "Failed restoring agent schedules on upgrade for user %s: %s",
                    getattr(owner, "id", "unknown"),
                    e,
                )

        return billing_record


def mark_user_billing_with_plan(user, plan_name: str, update_anchor: bool = True):
    return mark_owner_billing_with_plan(user, plan_name, update_anchor)


def mark_organization_billing_with_plan(organization, plan_name: str, update_anchor: bool = True):
    return mark_owner_billing_with_plan(organization, plan_name, update_anchor)


# ------------------------------------------------------------------------------
# Organization subscription helpers
# ------------------------------------------------------------------------------

def get_organization_plan(organization) -> dict[str, int | str]:
    """Return the plan configuration dictionary for an organization."""
    with traced("SUBSCRIPTION Get Organization Plan"):
        billing = getattr(organization, "billing", None)

        plan_key: str | None = None
        if billing and getattr(billing, "subscription", None):
            plan_key = billing.subscription
        elif getattr(organization, "plan", None):
            plan_key = organization.plan

        if not plan_key:
            plan_key = PlanNames.FREE

        plan_key = str(plan_key).lower()
        plan = PLAN_CONFIG.get(plan_key)

        if not plan:
            logger.warning(
                "get_organization_plan %s: Unknown plan '%s', defaulting to free",
                getattr(organization, "id", "n/a"),
                plan_key,
            )
            return PLAN_CONFIG[PlanNames.FREE]

        return plan


def get_organization_task_credit_limit(organization) -> int:
    """Return included monthly task credits for an organization (seats * credits)."""
    with traced("CREDITS Get Organization Task Credit Limit"):
        plan = get_organization_plan(organization)
        billing = getattr(organization, "billing", None)

        seats = 0
        if billing and getattr(billing, "purchased_seats", None):
            try:
                seats = int(billing.purchased_seats)
            except (TypeError, ValueError):
                seats = 0

        if seats <= 0:
            return 0

        credits_per_seat = plan.get("credits_per_seat")
        if credits_per_seat is not None:
            return int(credits_per_seat) * seats

        monthly = plan.get("monthly_task_credits") or 0
        return int(monthly)


def get_organization_extra_task_limit(organization) -> int:
    """Return the configured limit of additional tasks for an organization."""
    with traced("CREDITS Get Organization Extra Task Limit"):
        billing = getattr(organization, "billing", None)
        if not billing:
            logger.warning(
                "get_organization_extra_task_limit %s: Missing billing record; defaulting to 0",
                getattr(organization, "id", "n/a"),
            )
            return 0
        return getattr(billing, "max_extra_tasks", 0) or 0


def allow_organization_extra_tasks(organization) -> bool:
    """Return True when overage purchasing is enabled and subscription active."""
    with traced("CREDITS Allow Organization Extra Tasks"):
        limit = get_organization_extra_task_limit(organization)
        if limit <= 0 and limit != TASKS_UNLIMITED:
            return False

        billing = getattr(organization, "billing", None)
        if not billing or getattr(billing, "purchased_seats", 0) <= 0:
            return False

        cancel_at_period_end = getattr(billing, "cancel_at_period_end", False)
        return not cancel_at_period_end


def _get_org_billing_period(organization, today: date | None = None) -> tuple[date, date]:
    """Compute the current billing period (start, end) for an organization."""
    billing = getattr(organization, "billing", None)
    billing_day = 1
    if billing and getattr(billing, "billing_cycle_anchor", None):
        try:
            billing_day = int(billing.billing_cycle_anchor)
        except (TypeError, ValueError):
            billing_day = 1

    billing_day = min(max(billing_day, 1), 31)

    if today is None:
        today = timezone.now().date()

    this_month_candidate = today + relativedelta(day=billing_day)
    if this_month_candidate <= today:
        period_start = this_month_candidate
    else:
        period_start = (today - relativedelta(months=1)) + relativedelta(day=billing_day)

    next_period_start = period_start + relativedelta(months=1, day=billing_day)
    period_end = next_period_start - timedelta(days=1)
    return period_start, period_end


def calculate_org_extra_tasks_used_during_subscription_period(organization) -> int:
    """Return number of additional-task credits consumed in current billing period."""
    with traced("CREDITS Org Extra Tasks Used"):
        period_start, period_end = _get_org_billing_period(organization)
        tz = timezone.get_current_timezone()

        start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
        end_exclusive = timezone.make_aware(
            datetime.combine(period_end + timedelta(days=1), time.min), tz
        )

        TaskCredit = apps.get_model("api", "TaskCredit")
        task_credits = TaskCredit.objects.filter(
            organization=organization,
            granted_date__gte=start_dt,
            granted_date__lt=end_exclusive,
            additional_task=True,
            voided=False,
        )

        from django.db.models import Sum

        total_used = task_credits.aggregate(total=Sum('credits_used'))['total'] or 0
        try:
            return int(total_used)
        except Exception:
            return 0


def allow_and_has_extra_tasks_for_organization(organization) -> bool:
    """Return True if the organization may consume an additional-task credit now."""
    with traced("CREDITS Allow And Has Org Extra Tasks"):
        limit = get_organization_extra_task_limit(organization)

        if getattr(getattr(organization, "billing", None), "purchased_seats", 0) <= 0:
            return False

        if limit == TASKS_UNLIMITED:
            return allow_organization_extra_tasks(organization)

        if limit <= 0:
            return False

        used = calculate_org_extra_tasks_used_during_subscription_period(organization)
        return used < limit and allow_organization_extra_tasks(organization)


def get_user_extra_task_limit(user) -> int:
    """
    Gets the maximum number of extra tasks allowed for a user beyond their plan limits.

    This function retrieves the UserBilling record associated with the user and returns
    the max_extra_tasks value. If no UserBilling record exists for the user, it returns 0
    (indicating no extra tasks are allowed).

    Parameters:
        user (User): The user for whom the extra task limit is being fetched.

    Returns:
        int: The maximum number of extra tasks allowed.
             0 means no extra tasks are allowed.
             -1 means unlimited extra tasks are allowed - USE TASK_UNLIMITED CONSTANT
    """
    with traced("CREDITS Get User Extra Task Limit"):
        try:
            from api.models import UserBilling
            user_billing = UserBilling.objects.get(user=user)
            return user_billing.max_extra_tasks
        except UserBilling.DoesNotExist:
            logger.warning(f"get_user_extra_task_limit {user.id}: No UserBilling found, defaulting to 0")
            return 0

def allow_user_extra_tasks(user) -> bool:
    """
    Determines if a user is allowed to have extra tasks beyond their plan limits.

    This function checks the user's billing information to see if they have a positive
    max_extra_tasks value, which indicates that they can have extra tasks.

    Parameters:
        user (User): The user for whom the extra task allowance is being checked.

    Returns:
        bool: True if the user can have extra tasks, False otherwise.
    """
    with traced("CREDITS Allow User Extra Tasks"):
        task_limit = get_user_extra_task_limit(user)
        sub = get_active_subscription(user)

        if not sub:
            return False

        allow_based_on_subscription_status = not sub.cancel_at_period_end

        return (task_limit > 0 or task_limit == TASKS_UNLIMITED) and allow_based_on_subscription_status

def allow_and_has_extra_tasks(user) -> bool:
    """
    Checks if a user is allowed to have extra tasks and if they have any extra tasks.

    This function combines the checks for whether a user can have extra tasks and
    whether they currently have any extra tasks assigned.

    Parameters:
        user (User): The user for whom the extra task allowance and existence are being checked.

    Returns:
        bool: True if the user can have extra tasks and has at least one, False otherwise.
    """
    with traced("CREDITS Allow and Has Extra Tasks"):
        max_addl_tasks = get_user_extra_task_limit(user)

        if max_addl_tasks == TASKS_UNLIMITED:
            # Unlimited extra tasks allowed, so we assume they have some
            return True

        if max_addl_tasks > 0 and calculate_extra_tasks_used_during_subscription_period(user) < max_addl_tasks:
            # User is allowed to have extra tasks and has not exceeded their limit
            return True

        return False

def calculate_extra_tasks_used_during_subscription_period(user):
    """
    Calculates the number of extra tasks used by a user during their current subscription period.

    This function retrieves the user's active subscription and calculates the total number of extra tasks
    used based on the UsageRecord entries associated with the subscription. It sums up the quantity of
    extra tasks reported in these records.

    Parameters:
        user (User): The user for whom the extra tasks usage is being calculated.

    Returns:
        int: The total number of extra tasks used during the current subscription period.
    """
    with traced("CREDITS Calculate Extra Tasks Used During Subscription Period"):
        subscription = get_active_subscription(user)

        if not subscription:
            return 0

        sub_start = getattr(subscription.stripe_data, "current_period_start", None)
        sub_end = getattr(subscription.stripe_data, "current_period_end", None)

        if sub_start or not sub_end:
            return 0

        TaskCredit = apps.get_model("api", "TaskCredit")

        task_credits = TaskCredit.objects.filter(
            user=user,
            # make sure the task credit is within the subscription period using granted_date and expiration_date
            granted_date__gte=sub_start,
            expiration_date__lte=sub_end,
            additional_task=True,  # Only count additional tasks
            voided=False,  # Exclude voided task credits
        )
        from django.db.models import Sum
        total_used = task_credits.aggregate(total=Sum('credits_used'))['total'] or 0
        try:
            # Normalize to int for UI/percent calcs; current units are 1.0 per event
            return int(total_used)
        except Exception:
            return 0

def downgrade_owner_to_free_plan(owner):
    """Helper to mark any owner (user or organization) as free."""
    with traced("SUBSCRIPTION Downgrade Owner to Free Plan"):
        mark_owner_billing_with_plan(owner, PlanNames.FREE, False)


def downgrade_user_to_free_plan(user):
    downgrade_owner_to_free_plan(user)


def downgrade_organization_to_free_plan(organization):
    downgrade_owner_to_free_plan(organization)

def has_unlimited_agents(user) -> bool:
    """
    Checks if the user has unlimited agents based on their plan.

    This function retrieves the user's plan and checks if the agent limit is set to
    unlimited. If the user does not have a valid plan, it defaults to checking against
    the free plan's agent limit.

    Parameters:
        user (User): The user for whom the agent limit is being checked.

    Returns:
        bool: True if the user has unlimited agents, False otherwise.
    """
    with traced("SUBSCRIPTION Has Unlimited Agents"):
        plan = get_user_plan(user)

        if not plan:
            logger.warning(f"has_unlimited_agents {user.id}: No plan found, defaulting to free plan")
            return PLAN_CONFIG[PlanNames.FREE]["agent_limit"] == AGENTS_UNLIMITED

        return plan["agent_limit"] == AGENTS_UNLIMITED


def get_user_max_contacts_per_agent(user) -> int:
    """
    Returns the per‑agent contact cap for a user.

    Priority:
    1) If the user's UserQuota.max_agent_contacts is set (> 0), use it.
    2) Otherwise, fall back to the plan's max_contacts_per_agent (with sane defaults).
    """
    # Check for per-user override on quota
    try:
        from api.models import UserQuota
        quota = UserQuota.objects.filter(user=user).first()
        if quota and quota.max_agent_contacts is not None and quota.max_agent_contacts > 0:
            return int(quota.max_agent_contacts)
    except Exception as e:
        logger.error("get_user_max_contacts_per_agent: quota lookup failed for user %s: %s", getattr(user, 'id', 'n/a'), e)

    # Fallback to plan default
    plan = get_user_plan(user)
    if not plan:
        logger.warning(
            "get_user_max_contacts_per_agent %s: No plan found, defaulting to free plan",
            getattr(user, 'id', 'n/a')
        )
        return PLAN_CONFIG[PlanNames.FREE].get("max_contacts_per_agent", 3)

    return plan.get("max_contacts_per_agent", 3)
