from django.core.exceptions import AppRegistryNotReady

from config.stripe_config import get_stripe_settings
from constants.plans import PlanNames


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
        "price": 45,
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
        "price": 45,
        "price_per_seat": 45,
        "currency": "USD",
        "max_contacts_per_agent": 50,
        "org": True
    },

}


def _refresh_plan_products() -> None:
    """Update plan product IDs from StripeConfig storage."""
    try:
        stripe_settings = get_stripe_settings()
    except AppRegistryNotReady:
        return

    PLAN_CONFIG[PlanNames.STARTUP]["product_id"] = stripe_settings.startup_product_id or ""
    PLAN_CONFIG[PlanNames.STARTUP]["dedicated_ip_product_id"] = stripe_settings.startup_dedicated_ip_product_id or ""
    PLAN_CONFIG[PlanNames.STARTUP]["dedicated_ip_price_id"] = stripe_settings.startup_dedicated_ip_price_id or ""

    PLAN_CONFIG[PlanNames.SCALE]["product_id"] = stripe_settings.scale_product_id or ""
    PLAN_CONFIG[PlanNames.SCALE]["dedicated_ip_product_id"] = stripe_settings.scale_dedicated_ip_product_id or ""
    PLAN_CONFIG[PlanNames.SCALE]["dedicated_ip_price_id"] = stripe_settings.scale_dedicated_ip_price_id or ""

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
