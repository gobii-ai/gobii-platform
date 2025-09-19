import os


# Python has no int min constant, so we define our own
AGENTS_UNLIMITED = -2147483648
# Maximum number of agents any user can have, regardless of plan. Acts as a safety valve.
MAX_AGENT_LIMIT = 1000  # TODO: Adjust once we have confidence scaling beyond this
# NOTE: Keep this above AGENTS_UNLIMITED so comparisons using min() work correctly.

PLAN_CONFIG = {
    "free": {
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
    "startup": {
        "id": "startup",
        "monthly_task_credits": 500,
        "api_rate_limit": 600,
        "product_id": os.getenv("STRIPE_STARTUP_PRODUCT_ID", "prod_dummy_startup"),
        "agent_limit": AGENTS_UNLIMITED,
        "name": "Pro",
        "description": "Pro plan with enhanced features and support.",
        "price": 30,
        "currency": "USD",
        "max_contacts_per_agent": 20,
        "org": False
    },
    "org_team": {
        "id": "org_team",
        "monthly_task_credits": 2000,
        "credits_per_seat": 500,
        "api_rate_limit": 2000,
        "product_id": os.getenv("STRIPE_ORG_TEAM_PRODUCT_ID", "prod_dummy_org_team"),
        "agent_limit": AGENTS_UNLIMITED,
        "name": "Team",
        "description": "Team plan with collaboration features and priority support.",
        "price": 30,
        "price_per_seat": 30,
        "currency": "USD",
        "max_contacts_per_agent": 50,
        "org": True
    },

}

def get_plan_product_id(plan_name: str) -> str | None:
    """
    Returns the product ID for the given plan name.
    If the plan name is not found, returns None.
    """
    plan = PLAN_CONFIG.get(plan_name.lower())
    if plan:
        return plan["product_id"]
    return None

def get_plan_by_product_id(product_id: str) -> dict[str, int | str] | None:
    """
    Returns the plan name for the given product ID.
    If the product ID is not found, returns None.
    """
    for plan_name, config in PLAN_CONFIG.items():
        if config["product_id"] == product_id:
            return config

    return None
