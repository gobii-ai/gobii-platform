import logging

from django.apps import apps
from django.core.exceptions import AppRegistryNotReady
from django.db import OperationalError, ProgrammingError

from config.stripe_config import get_stripe_settings
from constants.plans import PlanNames

logger = logging.getLogger(__name__)


# Python has no int min constant, so we define our own
AGENTS_UNLIMITED = -2147483648
# Maximum number of agents any user can have, regardless of plan. Acts as a safety valve.
MAX_AGENT_LIMIT = 1000  # TODO: Adjust once we have confidence scaling beyond this
# NOTE: Keep this above AGENTS_UNLIMITED so comparisons using min() work correctly.

PLAN_CONFIG = {
    PlanNames.FREE: {
        "id": "free",
        "monthly_task_credits": 100,
        "api_rate_limit": 60,
        "product_id": "prod_free",
        "agent_limit": 5,
        "name": "Free",
        "description": "Free plan with basic features and limited support.",
        "price": 0,
        "currency": "USD",
        "max_contacts_per_agent": 3,
        "org": False
    },
    PlanNames.STARTUP: {
        "id": "startup",
        "monthly_task_credits": 500,
        "api_rate_limit": 600,
        "product_id": "",
        "dedicated_ip_product_id": "",
        "dedicated_ip_price_id": "",
        "dedicated_ip_price": 5,
        "agent_limit": AGENTS_UNLIMITED,
        "name": "Pro",
        "description": "Pro plan with enhanced features and support.",
        "price": 50,
        "currency": "USD",
        "max_contacts_per_agent": 20,
        "org": False
    },
    PlanNames.SCALE: {
        "id": PlanNames.SCALE,
        "monthly_task_credits": 10000,
        "api_rate_limit": 1500,
        "product_id": "",
        "dedicated_ip_product_id": "",
        "dedicated_ip_price_id": "",
        "dedicated_ip_price": 5,
        "agent_limit": AGENTS_UNLIMITED,
        "name": "Scale",
        "description": "Scale plan with enhanced limits and support.",
        "price": 250,
        "currency": "USD",
        "max_contacts_per_agent": 50,
        "org": False
    },
    PlanNames.ORG_TEAM: {
        "id": "org_team",
        "monthly_task_credits": 2000,
        "credits_per_seat": 500,
        "api_rate_limit": 2000,
        "product_id": "",
        "seat_price_id": "",
        "overage_price_id": "",
        "dedicated_ip_product_id": "",
        "dedicated_ip_price_id": "",
        "dedicated_ip_price": 5,
        "agent_limit": AGENTS_UNLIMITED,
        "name": "Team",
        "description": "Team plan with collaboration features and priority support.",
        "price": 50,
        "price_per_seat": 50,
        "currency": "USD",
        "max_contacts_per_agent": 50,
        "org": True
    },

}


def _get_price_amount(price_id: str, default: int) -> int:
    """Fetch price amount in dollars from dj-stripe Price model."""
    if not price_id:
        return default
    try:
        Price = apps.get_model("djstripe", "Price")
        price_obj = Price.objects.filter(id=price_id).first()
        if price_obj and price_obj.unit_amount is not None:
            return price_obj.unit_amount // 100  # Convert cents to dollars
    except (LookupError, OperationalError, ProgrammingError):
        pass
    except Exception as e:
        logger.debug("Error fetching price %s: %s", price_id, e)
    return default


def _refresh_plan_products() -> None:
    """Update plan product IDs and prices from StripeConfig and dj-stripe."""
    try:
        stripe_settings = get_stripe_settings()
    except AppRegistryNotReady:
        return

    PLAN_CONFIG[PlanNames.STARTUP]["product_id"] = stripe_settings.startup_product_id or ""
    PLAN_CONFIG[PlanNames.STARTUP]["dedicated_ip_product_id"] = stripe_settings.startup_dedicated_ip_product_id or ""
    PLAN_CONFIG[PlanNames.STARTUP]["dedicated_ip_price_id"] = stripe_settings.startup_dedicated_ip_price_id or ""
    PLAN_CONFIG[PlanNames.STARTUP]["price"] = _get_price_amount(
        stripe_settings.startup_price_id, default=50
    )

    PLAN_CONFIG[PlanNames.SCALE]["product_id"] = stripe_settings.scale_product_id or ""
    PLAN_CONFIG[PlanNames.SCALE]["dedicated_ip_product_id"] = stripe_settings.scale_dedicated_ip_product_id or ""
    PLAN_CONFIG[PlanNames.SCALE]["dedicated_ip_price_id"] = stripe_settings.scale_dedicated_ip_price_id or ""
    PLAN_CONFIG[PlanNames.SCALE]["price"] = _get_price_amount(
        stripe_settings.scale_price_id, default=250
    )

    PLAN_CONFIG[PlanNames.ORG_TEAM]["product_id"] = stripe_settings.org_team_product_id or ""
    PLAN_CONFIG[PlanNames.ORG_TEAM]["seat_price_id"] = stripe_settings.org_team_price_id or ""
    PLAN_CONFIG[PlanNames.ORG_TEAM]["overage_price_id"] = (
        stripe_settings.org_team_additional_task_price_id or ""
    )
    PLAN_CONFIG[PlanNames.ORG_TEAM]["dedicated_ip_product_id"] = (
        stripe_settings.org_team_dedicated_ip_product_id or ""
    )
    PLAN_CONFIG[PlanNames.ORG_TEAM]["dedicated_ip_price_id"] = (
        stripe_settings.org_team_dedicated_ip_price_id or ""
    )
    org_team_price = _get_price_amount(stripe_settings.org_team_price_id, default=50)
    PLAN_CONFIG[PlanNames.ORG_TEAM]["price"] = org_team_price
    PLAN_CONFIG[PlanNames.ORG_TEAM]["price_per_seat"] = org_team_price


def get_plan_product_id(plan_name: str) -> str | None:
    """
    Returns the product ID for the given plan name.
    If the plan name is not found, returns None.
    """
    _refresh_plan_products()
    plan = PLAN_CONFIG.get(plan_name.lower())
    if plan:
        return plan["product_id"]
    return None

def get_plan_by_product_id(product_id: str) -> dict[str, int | str] | None:
    """
    Returns the plan name for the given product ID.
    If the product ID is not found, returns None.
    """
    _refresh_plan_products()
    for plan_name, config in PLAN_CONFIG.items():
        if config["product_id"] == product_id:
            return config

    return None


def get_plan_config(plan_name: str) -> dict | None:
    """
    Returns the full plan configuration for the given plan name.
    Refreshes prices and product IDs from StripeConfig before returning.
    """
    _refresh_plan_products()
    return PLAN_CONFIG.get(plan_name.lower())
