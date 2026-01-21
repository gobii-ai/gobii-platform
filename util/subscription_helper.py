from django.contrib.auth import get_user_model
from django.db.models import IntegerField, Value
from django.db.models.expressions import OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.db.models.query_utils import Q
from django.db.utils import IntegrityError
from djstripe.models import Customer

from constants.grant_types import GrantTypeChoices
from config.plans import PLAN_CONFIG, AGENTS_UNLIMITED
from config.stripe_config import get_stripe_settings
from constants.plans import LEGACY_PLAN_BY_SLUG, PlanNames
from datetime import datetime, timedelta, date, time
from decimal import Decimal
from django.utils import timezone
import logging
import os
from typing import Literal, Tuple, Any
import uuid

from django.conf import settings
from observability import traced, trace
from util.constants.task_constants import TASKS_UNLIMITED
from util.payments_helper import PaymentsHelper
from util.integrations import stripe_status, IntegrationDisabledError
from djstripe.enums import SubscriptionStatus
from django.apps import apps
from dateutil.relativedelta import relativedelta
from billing.addons import AddonEntitlementService
from billing.plan_resolver import (
    get_owner_plan_context,
    get_plan_version_by_legacy_code,
)
from billing.services import BillingService

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


def _individual_plan_product_ids() -> set[str]:
    """Return product IDs for non-organization plans.

    This keeps the helper resilient to config churn by refreshing products
    before collecting the IDs.
    """
    try:
        from config import plans as plans_module

        plans_module._refresh_plan_products()
    except Exception:
        # Best-effort; if refresh fails we still try with current config values
        logger.debug("Failed to refresh plan products; using in-memory PLAN_CONFIG", exc_info=True)

    return {
        str(cfg.get("product_id"))
        for cfg in PLAN_CONFIG.values()
        if not cfg.get("org") and cfg.get("product_id")
    }


def _individual_plan_price_ids() -> set[str]:
    """Return licensed price IDs for non-organization plans."""
    try:
        stripe_settings = get_stripe_settings()
    except Exception:
        logger.debug("Failed to load stripe settings for plan prices", exc_info=True)
        return set()

    price_ids: set[str] = set()
    for price in (
        getattr(stripe_settings, "startup_price_id", None),
        getattr(stripe_settings, "scale_price_id", None),
    ):
        if price:
            price_ids.add(str(price))
    return price_ids


def _normalize_stripe_object(obj):
    """Convert Stripe objects to plain dicts for easier inspection."""
    if hasattr(obj, "to_dict_recursive"):
        try:
            return obj.to_dict_recursive()
        except Exception:
            logger.debug("Failed to normalize Stripe object; returning raw", exc_info=True)
    return obj


def get_existing_individual_subscriptions(customer_id: str) -> list[dict[str, Any]]:
    """Return all non-org subscriptions for a customer, newest first.

    A subscription is included when *any* item maps to a plan in PLAN_CONFIG
    with org == False. Cancelled/expired subscriptions are ignored so callers
    can focus on active/reattemptable states (trialing, active, incomplete, etc.).
    """
    if not customer_id:
        raise ValueError("customer_id is required")

    _ensure_stripe_ready()

    plan_products = _individual_plan_product_ids()
    plan_price_ids = _individual_plan_price_ids()
    if not plan_products and not plan_price_ids:
        logger.info("No individual plan products or prices configured; skipping subscription lookup")
        return []

    subscriptions: list[dict[str, Any]] = []

    try:
        iterator = stripe.Subscription.list(  # type: ignore[attr-defined]
            customer=customer_id,
            status="all",
            limit=100,
        ).auto_paging_iter()
    except Exception:
        logger.exception("Failed to list subscriptions for customer %s", customer_id)
        return []

    for sub in iterator:
        sub_data = _normalize_stripe_object(sub) or {}
        status = sub_data.get("status") or ""
        if status in ("canceled", "incomplete_expired"):
            continue

        items = (sub_data.get("items") or {}).get("data", []) or []
        for item in items:
            price = _normalize_stripe_object(item.get("price") or {}) or {}
            product = price.get("product")
            if isinstance(product, dict):
                product = product.get("id")

            price_id = price.get("id")

            if (product and product in plan_products) or (price_id and price_id in plan_price_ids):
                subscriptions.append(sub_data)
                break

    subscriptions.sort(key=lambda s: s.get("created") or 0, reverse=True)

    logger.info(
        "Found %s individual subscriptions for customer %s (plan products=%s)",
        len(subscriptions),
        customer_id,
        sorted(plan_products),
    )

    return subscriptions


def ensure_single_individual_subscription(
    customer_id: str,
    *,
    licensed_price_id: str,
    metered_price_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    create_if_missing: bool = True,
) -> tuple[Any | None, str]:
    """Ensure exactly one active individual subscription for a customer.

    Guardrails:
    - De-dupes any existing individual plan subscriptions by keeping the newest
      and immediately cancelling the rest.
    - Upgrades/downgrades reuse the existing subscription via Subscription.modify
      so we never spawn parallel subscriptions for the same customer.
    - Only creates a new subscription (base + optional metered item) when none exist.

    Returns (subscription, action) where action is "created", "updated",
    or "absent" (when create_if_missing is False and none exist).
    """

    if not customer_id:
        raise ValueError("customer_id is required")
    if not licensed_price_id:
        raise ValueError("licensed_price_id is required")

    _ensure_stripe_ready()

    metadata = metadata.copy() if metadata else {}
    idempotency_token = idempotency_key or f"ind-plan-{customer_id}-{licensed_price_id}-{uuid.uuid4()}"

    existing = get_existing_individual_subscriptions(customer_id)
    plan_products = _individual_plan_product_ids()

    # No existing subscription: optionally create one with the base + metered price
    if not existing:
        if not create_if_missing:
            logger.info(
                "No individual subscriptions found for customer %s; skipping creation (create_if_missing=False)",
                customer_id,
            )
            return None, "absent"

        items = [
            {"price": licensed_price_id, "quantity": 1},
        ]
        if metered_price_id:
            items.append({"price": metered_price_id})

        logger.info(
            "Creating new individual subscription for customer %s with items=%s",
            customer_id,
            [item.get("price") for item in items],
        )

        subscription = stripe.Subscription.create(  # type: ignore[attr-defined]
            customer=customer_id,
            items=items,
            metadata=metadata,
            idempotency_key=idempotency_token,
            expand=["items.data.price"],
        )

        return subscription, "created"

    newest = existing[0]
    stale = existing[1:]

    # Cancel duplicates immediately (no cancel-at-period-end queueing)
    for duplicate in stale:
        dup_id = duplicate.get("id")
        if not dup_id:
            continue
        try:
            logger.info(
                "Cancelling duplicate individual subscription %s for customer %s",
                dup_id,
                customer_id,
            )
            stripe.Subscription.delete(dup_id, prorate=True)  # type: ignore[attr-defined]
        except Exception:
            logger.warning(
                "Failed to cancel duplicate subscription %s for customer %s",
                dup_id,
                customer_id,
                exc_info=True,
            )

    # Build item updates while preserving unrelated add-ons
    existing_items = (newest.get("items") or {}).get("data", []) or []
    updated_items: list[dict[str, Any]] = []
    base_found = False
    meter_found = False

    for item in existing_items:
        price = _normalize_stripe_object(item.get("price") or {}) or {}
        product = price.get("product")
        if isinstance(product, dict):
            product = product.get("id")

        usage_type = price.get("usage_type") or (price.get("recurring") or {}).get("usage_type")

        payload: dict[str, Any] = {"id": item.get("id")}

        is_plan_product = product in plan_products
        is_plan_price = price.get("id") == licensed_price_id
        is_metered = (usage_type or "").lower() == "metered"

        if is_metered:
            target_price = metered_price_id or price.get("id")
            if not target_price:
                logger.warning("Metered subscription item %s has no price; skipping", item.get("id"))
                continue
            # Stripe ignores quantity for metered per-unit prices; omit to avoid InvalidRequestError.
            payload.update({"price": target_price})
            meter_found = meter_found or (metered_price_id == target_price)
        elif is_plan_product or is_plan_price:
            payload.update({"price": licensed_price_id, "quantity": item.get("quantity") or 1})
            base_found = True
        elif metered_price_id and price.get("id") == metered_price_id:
            payload.update({"price": metered_price_id})
            meter_found = True
        else:
            price_id = price.get("id")
            if not price_id:
                logger.warning("Subscription item %s has no price; skipping", item.get("id"))
                continue
            payload.update({"price": price_id})
            if item.get("quantity") is not None:
                payload["quantity"] = item.get("quantity")

        updated_items.append(payload)

    if not base_found:
        updated_items.append({"price": licensed_price_id, "quantity": 1})

    if metered_price_id and not meter_found:
        updated_items.append({"price": metered_price_id})

    merged_metadata = {**(newest.get("metadata") or {}), **metadata}

    logger.info(
        "Updating existing individual subscription %s for customer %s (items=%s)",
        newest.get("id"),
        customer_id,
        [item.get("price") for item in updated_items],
    )

    sub_id = newest.get("id")
    if not sub_id:
        raise ValueError(f"Subscription missing ID: {newest}")
    updated_sub = stripe.Subscription.modify(  # type: ignore[attr-defined]
        sub_id,
        items=updated_items,
        metadata=merged_metadata,
        idempotency_key=idempotency_token,
        expand=["items.data.price"],
        proration_behavior="always_invoice",
        payment_behavior="pending_if_incomplete",
    )

    return updated_sub, "updated"


def _ensure_stripe_ready() -> None:
    """Verify that the Stripe integration is usable and configure the API key."""
    status = stripe_status()
    if not status.enabled:
        raise IntegrationDisabledError(status.reason or "Stripe integration disabled")
    if stripe is None:  # type: ignore[truthy-function]
        raise IntegrationDisabledError("Stripe SDK not installed")
    key = PaymentsHelper.get_stripe_key()
    if not key:
        raise IntegrationDisabledError("Stripe secret key missing for current environment")
    stripe.api_key = key


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

def _subscription_products(sub) -> set[str]:
    products: set[str] = set()
    try:
        data = getattr(sub, "stripe_data", {}) or {}
        items = (data.get("items") or {}).get("data") or []
        for item in items:
            price = item.get("price") or {}
            product = price.get("product")
            if isinstance(product, dict):
                product = product.get("id")
            if isinstance(product, str) and product:
                products.add(product)
    except Exception:
        logger.debug("Failed to extract subscription products", exc_info=True)
    return products


def get_active_subscription(owner, *, preferred_plan_id: str | None = None) -> Subscription | None:
    """Fetch an active licensed subscription, preferring one that carries the base plan product."""
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

        preferred_products: set[str] = set()
        if preferred_plan_id:
            try:
                preferred_products.add(str(PLAN_CONFIG.get(preferred_plan_id, {}).get("product_id") or ""))
            except Exception:
                preferred_products = set()
        preferred_products = {p for p in preferred_products if p}

        plan_products = {str(cfg.get("product_id")) for cfg in PLAN_CONFIG.values() if cfg.get("product_id")}

        def _sort_key(sub):
            products = _subscription_products(sub)
            preferred_match = 0 if (preferred_products and products.intersection(preferred_products)) else 1
            plan_match = 0 if products.intersection(plan_products) else 1
            cancel_flag = 1 if sub.stripe_data.get("cancel_at_period_end") else 0
            period_end = sub.stripe_data.get("current_period_end") or 0
            return (preferred_match, plan_match, cancel_flag, period_end)

        subs.sort(key=_sort_key)

        return subs[0] if subs else None


def get_subscription_base_price(subscription) -> tuple[Decimal | None, str | None]:
    """Return (unit_amount, currency) for the base (non-metered, non-add-on) item on a subscription."""
    if subscription is None:
        return None, None

    def _normalize_product_id(value: Any) -> str | None:
        """Return prod_* ID when present; ignore display names or non-IDs."""
        try:
            if hasattr(value, "id"):
                value = getattr(value, "id", None)
        except Exception:
            pass

        if isinstance(value, dict):
            value = value.get("id")

        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("prod_"):
                return candidate
        return None

    plan_products = {str(cfg.get("product_id")) for cfg in PLAN_CONFIG.values() if cfg.get("product_id")}
    excluded_products: set[str] = set()
    try:
        stripe_settings = get_stripe_settings()
        for attr in (
            "startup_dedicated_ip_product_id",
            "scale_dedicated_ip_product_id",
            "org_team_dedicated_ip_product_id",
        ):
            pid = _normalize_product_id(getattr(stripe_settings, attr, None))
            if pid:
                excluded_products.add(pid)
    except Exception:
        logger.debug("Failed to load stripe settings for base price detection", exc_info=True)

    preferred_amount = None
    preferred_currency = None
    fallback_amount = None
    fallback_currency = None
    try:
        items_qs = getattr(subscription, "items", None)
        items = list(items_qs.all()) if hasattr(items_qs, "all") else []
    except Exception:
        logger.debug("Failed to load subscription items for %s", getattr(subscription, "id", None), exc_info=True)
        return None, None

    for item in items:
        price_obj = getattr(item, "price", None)
        item_data = getattr(item, "stripe_data", {}) or {}
        price_data = (item_data.get("price") if isinstance(item_data.get("price"), dict) else None) or {}

        usage_type = None
        try:
            recurring = getattr(price_obj, "recurring", None)
            if recurring and hasattr(recurring, "get"):
                candidate = recurring.get("usage_type")
                if isinstance(candidate, str):
                    usage_type = candidate
        except Exception:
            logger.debug("Failed to extract usage_type from price_obj for subscription %s", getattr(subscription, "id", None), exc_info=True)
        if not usage_type:
            usage_type = price_data.get("recurring", {}).get("usage_type") or price_data.get("usage_type")

        if (usage_type or "").lower() == "metered":
            continue

        product_candidate = price_data.get("product")
        if product_candidate is None and hasattr(price_obj, "product"):
            product_candidate = getattr(price_obj, "product", None)
        product_id = _normalize_product_id(product_candidate)

        if product_id and product_id in excluded_products:
            continue

        currency = getattr(price_obj, "currency", None) or price_data.get("currency")

        unit_amount = getattr(price_obj, "unit_amount", None)
        if unit_amount is None:
            unit_amount = price_data.get("unit_amount")
        if unit_amount is None and "unit_amount_decimal" in price_data:
            try:
                unit_amount = Decimal(price_data["unit_amount_decimal"])
            except Exception:
                logger.debug("Failed to parse unit_amount_decimal for subscription %s", getattr(subscription, "id", None), exc_info=True)

        if unit_amount is None:
            continue

        if product_id and product_id in plan_products:
            preferred_amount, preferred_currency = unit_amount, currency
            break
        elif fallback_amount is None:
            fallback_amount, fallback_currency = unit_amount, currency

    target_amount = preferred_amount if preferred_amount is not None else fallback_amount
    target_currency = preferred_currency if preferred_currency is not None else fallback_currency

    if target_amount is None:
        return None, None

    try:
        amount_decimal = Decimal(target_amount) / Decimal("100")
        return amount_decimal, target_currency
    except Exception:
        logger.debug(
            "Failed to coerce unit_amount=%s for subscription %s", target_amount, getattr(subscription, "id", None)
        )

    return None, None

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
        logger.debug("get_owner_plan %s %s", owner_type, owner_id)

        try:
            plan_context = get_owner_plan_context(owner)
            if plan_context:
                return plan_context
        except Exception:
            logger.warning(
                "get_owner_plan %s: failed to resolve plan context; falling back to legacy config",
                owner_id,
                exc_info=True,
            )

        billing_record = _get_billing_record(owner)
        sub_name = getattr(billing_record, "subscription", None) if billing_record else None
        sub_key = str(sub_name).lower() if sub_name else PlanNames.FREE
        return PLAN_CONFIG.get(sub_key, PLAN_CONFIG[PlanNames.FREE])

def get_user_plan(user) -> dict[str, int | str]:
    return get_owner_plan(user)


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
        _ensure_stripe_ready()

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
                api_key=stripe.api_key,
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
            _ensure_stripe_ready()
            logger.debug(
                f"report_usage_to_stripe: Reporting {quantity} usage for user {user.id} on meter {meter_id}"
            )

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
        except IntegrationDisabledError as exc:
            logger.info("Stripe disabled; skipping user usage report: %s", exc)
            return None
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
            _ensure_stripe_ready()
            meter_event = stripe.billing.MeterEvent.create(
                event_name=stripe_settings.task_meter_event_name,
                payload={"value": quantity, "stripe_customer_id": billing.stripe_customer_id},
                idempotency_key=idempotency_key,
            )
            return meter_event
        except IntegrationDisabledError as exc:
            logger.info("Stripe disabled; skipping organization usage report: %s", exc)
            return None
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
            _ensure_stripe_ready()
            stripe_settings = get_stripe_settings()

            with traced("STRIPE Create Meter Event"):
                meter_event = stripe.billing.MeterEvent.create(
                    event_name=stripe_settings.task_meter_event_name,
                    payload={"value": quantity, "stripe_customer_id": subscription.customer.id},
                    idempotency_key=idempotency_key,
                )
                return meter_event
        except IntegrationDisabledError as exc:
            logger.info("Stripe disabled; skipping meter event creation: %s", exc)
            return None
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

def get_users_due_for_monthly_grant(days: int = 35):
    """
    Return users who are due for their free monthly task credit grant.

    A user is considered due when their current billing period has started but
    they do not yet have a `Plan` task credit recorded for that period. The
    billing cycle anchor stored on `UserBilling` determines when each period
    begins (defaulting to the 1st if no record exists). Anchors beyond the
    length of the month (e.g., 31) automatically roll to the last day of the
    current month via `BillingService.get_current_billing_period_from_day`.

    The optional ``days`` argument defines how far back we consider period
    starts. This keeps the helper useful for catch-up runs if a scheduled job
    misses its usual execution window while still avoiding scanning the entire
    history on every invocation.
    """
    with traced("CREDITS Get Users Due For Monthly Grant"):
        TaskCredit = apps.get_model("api", "TaskCredit")
        UserBilling = apps.get_model("api", "UserBilling")
        User = get_user_model()

        latest_grant = Subquery(
            TaskCredit.objects.filter(
                user=OuterRef("pk"),
                grant_type=GrantTypeChoices.PLAN,
                voided=False,
            )
            .order_by("-granted_date")
            .values("granted_date")[:1]
        )

        billing_anchor = Subquery(
            UserBilling.objects.filter(user=OuterRef("pk")).values("billing_cycle_anchor")[:1],
            output_field=IntegerField(),
        )

        today = timezone.now().date()
        window_start = today - timedelta(days=max(days - 1, 0))

        annotated_users = (
            User.objects.filter(is_active=True)
            .filter(Q(billing__subscription=PlanNames.FREE) | Q(billing__isnull=True))
            .annotate(
                billing_day=Coalesce(billing_anchor, Value(1, output_field=IntegerField())),
                last_grant_date=latest_grant,
            )
        )

        due_users: list = []
        for user in annotated_users.iterator():
            billing_day_int = getattr(user, "billing_day", 1)

            billing_day_int = max(1, min(31, billing_day_int))
            period_start, _ = BillingService.get_current_billing_period_from_day(billing_day_int, today)

            if period_start < window_start:
                continue

            last_grant = getattr(user, "last_grant_date", None)
            last_grant_date = last_grant.date() if last_grant else None

            if last_grant_date is None or last_grant_date < period_start:
                due_users.append(user)

        return due_users

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

def mark_owner_billing_with_plan(owner, plan_name: str, update_anchor: bool = True, plan_version=None):
    """Persist the selected plan on the owner billing record (user or organization)."""
    with traced("SUBSCRIPTION Mark Billing with Plan") as span:
        owner_type = _resolve_owner_type(owner)
        owner_id = getattr(owner, "id", None) or getattr(owner, "pk", None)
        span.set_attribute("owner.type", owner_type)
        if owner_id is not None:
            span.set_attribute("owner.id", str(owner_id))
        span.set_attribute("update_anchor", str(update_anchor))

        normalized_plan_name = str(plan_name).lower() if plan_name else None
        if normalized_plan_name:
            plan_name = LEGACY_PLAN_BY_SLUG.get(normalized_plan_name, plan_name)

        if plan_version is None and plan_name:
            plan_version = get_plan_version_by_legacy_code(plan_name)

        defaults = {"subscription": plan_name}
        if plan_version is not None:
            defaults["plan_version"] = plan_version
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

                (
                    PersistentAgent.objects
                    .filter(user=owner)
                    .exclude(daily_credit_limit__isnull=True)
                    .update(daily_credit_limit=None)
                )

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


def mark_user_billing_with_plan(user, plan_name: str, update_anchor: bool = True, plan_version=None):
    return mark_owner_billing_with_plan(user, plan_name, update_anchor, plan_version=plan_version)


def mark_organization_billing_with_plan(organization, plan_name: str, update_anchor: bool = True, plan_version=None):
    return mark_owner_billing_with_plan(organization, plan_name, update_anchor, plan_version=plan_version)


# ------------------------------------------------------------------------------
# Organization subscription helpers
# ------------------------------------------------------------------------------

def get_organization_plan(organization) -> dict[str, int | str]:
    """Return the plan configuration dictionary for an organization."""
    with traced("SUBSCRIPTION Get Organization Plan"):
        billing = getattr(organization, "billing", None)
        if billing and (getattr(billing, "plan_version", None) or getattr(billing, "subscription", None)):
            return get_owner_plan(organization)

        plan_key = getattr(organization, "plan", None) or PlanNames.FREE
        plan_key = str(plan_key).lower()
        plan = PLAN_CONFIG.get(plan_key)
        if plan:
            return plan

        logger.warning(
            "get_organization_plan %s: Unknown plan '%s', defaulting to free",
            getattr(organization, "id", "n/a"),
            plan_key,
        )
        return PLAN_CONFIG[PlanNames.FREE]


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


def is_community_unlimited_mode() -> bool:
    """Return True when Community Edition should ignore plan agent limits."""
    try:
        if 'test_settings' in os.environ.get('DJANGO_SETTINGS_MODULE', ''):
            return False
        return (not getattr(settings, "GOBII_PROPRIETARY_MODE", False)) and bool(
            getattr(settings, "GOBII_ENABLE_COMMUNITY_UNLIMITED", False)
        )
    except Exception:
        return False


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
        if is_community_unlimited_mode():
            return True

        plan = get_user_plan(user)

        if not plan:
            logger.warning(f"has_unlimited_agents {user.id}: No plan found, defaulting to free plan")
            return PLAN_CONFIG[PlanNames.FREE]["agent_limit"] == AGENTS_UNLIMITED

        return plan["agent_limit"] == AGENTS_UNLIMITED


def get_user_max_contacts_per_agent(user, organization=None) -> int:
    """Return the per-agent contact cap for a user or organization-owned agent.

    In community mode (non-proprietary), disable contact caps.

    Priority when ``organization`` is provided:
    1) Use the organization's plan ``max_contacts_per_agent`` value.
    2) Fall back to the free-plan default when unavailable.

    Priority for individual users:
    1) If the user's ``UserBilling.max_contacts_per_agent`` override is set (>0), use it.
    2) Otherwise, if ``UserQuota.max_agent_contacts`` is set (>0), use that legacy override.
    3) When neither is set, fall back to the user's plan ``max_contacts_per_agent`` (defaulting to the free plan).
    """
    if not getattr(settings, "GOBII_PROPRIETARY_MODE", False):
        return 0

    default_limit = PLAN_CONFIG[PlanNames.FREE].get("max_contacts_per_agent", 3)

    addon_uplift = AddonEntitlementService.get_contact_cap_uplift(organization or user)
    def _with_addon(value: int | None) -> int:
        try:
            base = int(value or 0)
        except (TypeError, ValueError):
            base = 0
        return base + addon_uplift

    if organization is not None:
        plan = get_organization_plan(organization)
        if not plan:
            logger.warning(
                "get_user_max_contacts_per_agent org %s: No plan found, defaulting to free plan",
                getattr(organization, 'id', 'n/a'),
            )
            return _with_addon(default_limit)

        try:
            base_limit = int(plan.get("max_contacts_per_agent", default_limit))
        except (ValueError, TypeError):
            base_limit = default_limit

        return _with_addon(base_limit)

    # Check for per-user override stored on billing
    try:
        from api.models import UserBilling
        billing_record = (
            UserBilling.objects
            .only('max_contacts_per_agent')
            .filter(user=user)
            .first()
        )
        if (
            billing_record
            and billing_record.max_contacts_per_agent is not None
            and billing_record.max_contacts_per_agent > 0
        ):
            return _with_addon(billing_record.max_contacts_per_agent)
    except Exception as e:
        logger.error(
            "get_user_max_contacts_per_agent: billing lookup failed for user %s: %s",
            getattr(user, 'id', 'n/a'),
            e,
        )

    # Check for older per-user override on quota model
    try:
        from api.models import UserQuota
        quota = UserQuota.objects.filter(user=user).first()
        if quota and quota.max_agent_contacts is not None and quota.max_agent_contacts > 0:
            return _with_addon(quota.max_agent_contacts)
    except Exception as e:
        logger.error(
            "get_user_max_contacts_per_agent: quota lookup failed for user %s: %s",
            getattr(user, 'id', 'n/a'),
            e,
        )

    # Fallback to plan default
    plan = get_user_plan(user)
    if not plan:
        logger.warning(
            "get_user_max_contacts_per_agent %s: No plan found, defaulting to free plan",
            getattr(user, 'id', 'n/a')
        )
        return _with_addon(default_limit)

    try:
        base_limit = int(plan.get("max_contacts_per_agent", default_limit))
    except (ValueError, TypeError):
        base_limit = default_limit

    return _with_addon(base_limit)
