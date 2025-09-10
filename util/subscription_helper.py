from django.contrib.auth import get_user_model
from django.db.models.expressions import OuterRef, Subquery
from django.db.models.query_utils import Q
from django.db.utils import IntegrityError
from djstripe.models import Customer

from constants.grant_types import GrantTypeChoices
from config import settings
from config.plans import PLAN_CONFIG, get_plan_by_product_id, AGENTS_UNLIMITED
from constants.plans import PlanNames
from datetime import datetime, timedelta, date
from django.utils import timezone
import logging

from observability import traced, trace
from util.constants.task_constants import TASKS_UNLIMITED
from util.payments_helper import PaymentsHelper
from djstripe.enums import SubscriptionStatus
from django.apps import apps

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

def get_stripe_customer(user) -> Customer | None:
    """
    Retrieves a Stripe Customer object associated with a specific user. If the user
    does not have an associated Stripe Customer, None is returned.

    Parameters
    ----------
    user : User
        The user for whom the Stripe Customer object should be retrieved.

    Returns
    -------
    Customer | None
        The Stripe Customer object associated with the user if it exists, otherwise
        None.
    """
    with traced("SUBSCRIPTION - Get Stripe Customer"):
        try:
            return Customer.objects.get(subscriber=user)
        except Customer.DoesNotExist:
            return None

def get_active_subscription(user) -> Subscription | None:
    """
    Fetch the first active licensed subscription for a given user.

    This function tries to retrieve the Stripe customer associated with the provided
    user. If found, it filters the customer's active subscriptions to find those with
    a plan usage type marked as 'licensed'. If no subscriptions or customers are found,
    it returns None. The licensed check is so additional metered uses (extra tasks) do
    not show as having an active subscription.

    Parameters:
    user: User
        The user whose active licensed subscription needs to be fetched.

    Returns:
    Subscription or None
        Returns the first active licensed subscription if such a subscription exists,
        otherwise returns None.
    """
    with traced("SUBSCRIPTION - Get Active Subscription") as span:
        span.set_attribute("user.id", user.id)
        customer = get_stripe_customer(user)
        logger.debug(f"get_active_subscription {user.id}: {customer}")

        if not customer:
            logger.debug(f"get_active_subscription {user.id}: No customer found")
            span.set_attribute("user.customer", "")
            return None
        else:
            span.set_attribute("user.customer.id", str(customer.id))
            logger.debug(f"get_active_subscription {user.id} subscriptions: {customer.active_subscriptions}")

        # @var customer.active_subscriptions: QuerySet
        licensed_subs = customer.active_subscriptions.order_by("cancel_at_period_end")

        return licensed_subs.first() if licensed_subs else None

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

def get_user_plan(user) -> dict[str, int | str]:
    """
    Fetches the user plan based on the active subscription associated with a user.

    This function checks if the user has an active subscription. If not, or if the
    active subscription does not have a valid plan or product associated with it,
    it defaults to returning a free plan configuration. Otherwise, it fetches and
    returns the plan derived from the product ID associated with the user's active
    subscription.

    Parameters:
    user (User): The user object whose plan is being retrieved.

    Returns:
    dict[str, int | str]: A dictionary representing the plan configuration for the
    user. Defaults to the free plan if no valid subscription or plan is found.
    """
    with traced("SUBSCRIPTION Get User Plan"):
        subscription = get_active_subscription(user)

        logger.debug(f"get_user_plan {user.id}: {subscription}")

        if not subscription:
            logger.debug(f"get_user_plan {user.id}: No active subscription found")
            return PLAN_CONFIG[PlanNames.FREE]

        # Absolutely ridiculous but this is how dj-stripe works
        stripe_sub = subscription.stripe_data

        product_id = None
        for item_data in stripe_sub.get("items", {}).get("data", []):
            if item_data.get("plan", {}).get("usage_type") == "licensed":
                product_id = item_data.get("price", {}).get("product")
                break # Found the licensed item, no need to check further

        logger.debug(f"get_user_plan {user.id} product_id: {product_id}")

        if not product_id:
            logger.warning(f"get_user_plan {user.id}: Subscription product is None")
            return PLAN_CONFIG[PlanNames.FREE]

        plan = get_plan_by_product_id(product_id)

        return plan if plan else PLAN_CONFIG[PlanNames.FREE]

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

def get_or_create_stripe_customer(user) -> Customer:
    """
    Retrieves an existing Stripe customer associated with the given user or creates
    a new Stripe customer if none exists. Synchronizes the created Stripe customer's
    data with the application's database.

    Parameters:
    user : User
        The user instance for whom the Stripe customer is to be retrieved or created.

    Returns:
    Customer
        An instance of the Customer model representing the associated Stripe customer.
    """
    with traced("SUBSCRIPTION Get or Create Stripe Customer"):
        customer = Customer.objects.filter(subscriber=user).first()
        if customer:
            return customer

        # Create the customer on Stripe
        with traced("STRIPE Create Customer"):
            stripe_customer = stripe.Customer.create(
                email=user.email,
                metadata={"user_id": user.pk},  # helpful for later troubleshooting
            )

        # Write the dj-stripe row and attach it
        customer = Customer.sync_from_stripe_data(stripe_customer)
        customer.subscriber = user
        customer.save(update_fields=["subscriber"])
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

def report_task_usage_to_stripe(user, quantity: int = 1, meter_id=settings.STRIPE_TASK_METER_ID):
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
        defaults to settings.STRIPE_TASK_METER_ID.

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

        # Use default meter ID if none provided
        if meter_id is None:
            meter_id = settings.STRIPE_TASK_METER_ID

        # Create the usage record in Stripe
        try:
            logger.debug(
                f"report_usage_to_stripe: Reporting {quantity} usage for user {user.id} on meter {meter_id}"
            )

            stripe.api_key = PaymentsHelper.get_stripe_key()
            report_task_usage(subscription, quantity=quantity)

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

def report_task_usage(subscription: Subscription, quantity: int = 1):
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

            with traced("STRIPE Create Meter Event"):
                meter_event = stripe.billing.MeterEvent.create(
                    event_name=settings.STRIPE_TASK_METER_EVENT_NAME,
                    payload={"value": quantity, "stripe_customer_id": subscription.customer.id},
                )

        except Exception as e:
            logger.error(f"report_task_usage: Error reporting task usage: {str(e)}")

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

def mark_user_billing_with_plan(user, plan_name: str, update_anchor: bool = True):
    """
    Marks a user as having a specific billing plan.

    This function updates the user's billing information to reflect the specified plan.
    It is typically used when a user subscribes to a new plan or changes their existing plan.

    Parameters:
    ----------
    user : User
        The user whose billing information is being updated.
    plan_name : str
        A string representing the name of the plan to be associated with the user.

    update_anchor : bool, optional
        A boolean indicating whether to update the billing cycle anchor to the current day.

    Returns:
    -------
    None
        This function does not return any value.
    """
    with traced("SUBSCRIPTION Mark User Billing with Plan") as span:
        UserBilling = apps.get_model("api", "UserBilling")

        defaults = {
            'subscription': plan_name,
        }

        span.set_attribute('update_anchor', str(update_anchor))

        if update_anchor:
            defaults['billing_cycle_anchor'] = timezone.now().day

        billing_record, created = UserBilling.objects.get_or_create(
            user=user,
            defaults=defaults
        )
        prev_plan = billing_record.subscription if not created else None
        if not created:
            # If the record already existed, update it with the new values from defaults.
            for key, value in defaults.items():
                setattr(billing_record, key, value)
            # Set downgrade timestamp if moving to free; clear otherwise
            from constants.plans import PlanNames
            if prev_plan and prev_plan != PlanNames.FREE and plan_name == PlanNames.FREE:
                billing_record.downgraded_at = timezone.now()
            elif plan_name != PlanNames.FREE:
                billing_record.downgraded_at = None
            update_fields = list(defaults.keys()) + ["downgraded_at"]
            billing_record.save(update_fields=update_fields)
        else:
            # New record; initialize downgrade timestamp if free
            from constants.plans import PlanNames
            if plan_name == PlanNames.FREE:
                billing_record.downgraded_at = timezone.now()
                billing_record.save(update_fields=["downgraded_at"])

        span.add_event('Subscription - Updated', {
            'user.id': user.id,
            'plan.name': plan_name
        })

        # If upgrading to a paid plan, restore any soft-expired agent schedules
        try:
            from constants.plans import PlanNames
            if plan_name != PlanNames.FREE:
                from api.models import PersistentAgent
                agents = (
                    PersistentAgent.objects
                    .filter(user=user, life_state=PersistentAgent.LifeState.EXPIRED)
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
            logger.error("Failed restoring agent schedules on upgrade for user %s: %s", user.id, e)

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
        sub_start = subscription.current_period_start if subscription else None
        sub_end = subscription.current_period_end if subscription else None

        if not subscription or not sub_start or not sub_end:
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

        addl_task_count = task_credits.count()

        return addl_task_count

def downgrade_user_to_free_plan(user):
    """
    Downgrades the user's plan to the free plan.

    This function updates the user's billing information to reflect the free plan.
    It is typically used when a user cancels their subscription or downgrades to
    a free tier.

    Parameters:
    ----------
    user : User
        The user whose plan is being downgraded.

    Returns:
    -------
    None
        This function does not return any value.
    """
    with traced("SUBSCRIPTION Downgrade User to Free Plan") as span:
        mark_user_billing_with_plan(user, PlanNames.FREE, False)  # Downgrade to free plan

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
    Gets the maximum number of contacts allowed per agent based on the user's plan.
    
    Parameters:
        user (User): The user for whom the contact limit is being retrieved.
    
    Returns:
        int: The maximum number of contacts allowed per agent for the user's plan.
    """
    plan = get_user_plan(user)
    
    if not plan:
        logger.warning(f"get_user_max_contacts_per_agent {user.id}: No plan found, defaulting to free plan")
        return PLAN_CONFIG[PlanNames.FREE].get("max_contacts_per_agent", 3)
    
    return plan.get("max_contacts_per_agent", 3)  # Default to 3 if not specified
