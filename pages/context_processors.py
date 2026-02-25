import hashlib
import logging
from hashlib import sha256
from datetime import datetime
from agents.services import AgentService
from django.conf import settings as django_settings
from django.core.cache import cache
from django.http import HttpRequest
from django.utils import timezone
from api.agent.core.llm_config import is_llm_bootstrap_required
from config import settings
from config.plans import AGENTS_UNLIMITED
from constants.plans import PlanNames
from pages.account_info_cache import (
    account_info_cache_key,
    account_info_cache_lock_key,
)
from pages.mini_mode import is_mini_mode_enabled
from tasks.services import TaskCreditService
from util.analytics import AnalyticsEvent, AnalyticsCTAs, Analytics
from util.fish_collateral import is_fish_collateral_enabled
from util.subscription_helper import (
    get_user_plan,
    get_user_api_rate_limit,
    get_user_agent_limit,
    get_user_task_credit_limit,
    has_unlimited_agents,
    allow_user_extra_tasks,
    get_user_extra_task_limit,
    get_user_max_contacts_per_agent,
)
from util.tool_costs import get_most_expensive_tool_cost
from util.constants.task_constants import TASKS_UNLIMITED


def _enum_to_dict(enum_cls):
    """{'ENUM_MEMBER': 'string value', ...}"""
    return {member.name: member.value for member in enum_cls}

logger = logging.getLogger(__name__)

ACCOUNT_INFO_CACHE_FRESH_SECONDS = 45
ACCOUNT_INFO_CACHE_STALE_SECONDS = 600
ACCOUNT_INFO_CACHE_LOCK_SECONDS = 60


def sha256_hex(value: str | None) -> str:
    """
    Lower-case, trim, encode UTF-8, then return hex digest.
    Empty string if value is None / blank.
    """
    if not value:
        return ""
    normalised = value.strip().lower().encode("utf-8")
    return hashlib.sha256(normalised).hexdigest()


def _build_account_info(user):
    # Get the user's plan and subscription details
    plan = get_user_plan(user)
    agents_unlimited = has_unlimited_agents(user) or ()

    paid_plan = plan["id"] != PlanNames.FREE

    # Get the user's task credits - there are multiple calls below that we can recycle this in to save on DB calls
    task_credits = TaskCreditService.get_current_task_credit(user)
    tasks_available = TaskCreditService.get_user_task_credits_available(user, task_credits=task_credits)
    max_task_cost = get_most_expensive_tool_cost()

    # Determine if the user effectively has unlimited tasks (e.g., unlimited additional tasks)
    tasks_entitled = TaskCreditService.get_tasks_entitled(user)
    tasks_unlimited = tasks_entitled == TASKS_UNLIMITED

    acct_info = {
        "account": {
            "plan": plan,
            "paid": paid_plan,
            "usage": {
                "rate_limit": get_user_api_rate_limit(user),
                "agent_limit": get_user_agent_limit(user),
                "agents_unlimited": agents_unlimited,
                "agents_in_use": AgentService.get_agents_in_use(user),
                "agents_available": AGENTS_UNLIMITED
                if agents_unlimited is True
                else AgentService.get_agents_available(user),
                "tasks_entitled": tasks_entitled,
                "tasks_available": tasks_available,
                # If unlimited, usage is effectively 0%; else treat "can't afford a single tool" as 100%
                "tasks_used_pct": (
                    0
                    if (tasks_unlimited or tasks_available == TASKS_UNLIMITED)
                    else (
                        100
                        if tasks_available < max_task_cost
                        else TaskCreditService.get_user_task_credits_used_pct(
                            user,
                            task_credits=task_credits,
                        )
                    )
                ),
                "tasks_addl_enabled": allow_user_extra_tasks(user),
                "tasks_addl_limit": get_user_extra_task_limit(user),
                "task_credits_monthly": get_user_task_credit_limit(user),
                "task_credits_available": TaskCreditService.calculate_available_tasks(
                    user,
                    task_credits=task_credits,
                ),
                "max_contacts_per_agent": get_user_max_contacts_per_agent(user),
            },
        }
    }

    return acct_info


def _enqueue_account_info_refresh(user_id: object) -> None:
    lock_key = account_info_cache_lock_key(user_id)
    if not cache.add(lock_key, "1", timeout=ACCOUNT_INFO_CACHE_LOCK_SECONDS):
        return

    try:
        from pages.tasks import refresh_account_info_cache

        refresh_account_info_cache.delay(str(user_id))
    except Exception:
        cache.delete(lock_key)
        logger.exception("Failed to enqueue account info refresh for user %s", user_id)


def account_info(request):
    """
    Adds account info to every template so you can write
        {% if account.has_free_agent_slots %} … {% endif %}
    """
    if not request.user.is_authenticated:
        return {}  # skip work for anonymous users

    user = request.user
    cache_key = account_info_cache_key(user.id)
    cached = cache.get(cache_key)
    now_ts = timezone.now().timestamp()

    if isinstance(cached, dict):
        cached_data = cached.get("data")
        refreshed_at = cached.get("refreshed_at")
        if cached_data is not None and refreshed_at is not None:
            age_seconds = max(0, now_ts - refreshed_at)
            if age_seconds <= ACCOUNT_INFO_CACHE_FRESH_SECONDS:
                return cached_data
            if age_seconds <= ACCOUNT_INFO_CACHE_STALE_SECONDS:
                _enqueue_account_info_refresh(user.id)
                return cached_data

    acct_info = _build_account_info(user)
    cache.set(
        cache_key,
        {"data": acct_info, "refreshed_at": now_ts},
        timeout=ACCOUNT_INFO_CACHE_STALE_SECONDS,
    )

    return acct_info


def environment_info(request):
    """
    Adds environment info to every template so you can write
        {% if environment.is_production %} … {% endif %}
    """
    release_env = getattr(
        django_settings,
        "GOBII_RELEASE_ENV",
        getattr(settings, "GOBII_RELEASE_ENV", "local"),
    )
    return {
        'environment': {
            'is_production': release_env.lower() in ('prod', 'production'),
        }
    }


def show_signup_tracking(request):
    """
    Adds a flag to the context to control whether to show signup tracking.
    This is set in the user_signed_up signal handler.
    """
    return {
        'show_signup_tracking': request.session.get('show_signup_tracking', False),
        'signup_event_id': request.session.get('signup_event_id'),
        'signup_user_id': request.session.get('signup_user_id'),
        'signup_email_hash': request.session.get('signup_email_hash'),
    }


def mini_mode(request):
    mini_mode_enabled = is_mini_mode_enabled(request)
    return {
        "mini_mode_enabled": mini_mode_enabled,
        "mini_mode_solutions_header": mini_mode_enabled and request.path.startswith("/solutions/"),
    }


def fish_collateral(request):
    return {
        "fish_collateral_enabled": is_fish_collateral_enabled(),
    }


def analytics(request):
    """
    Adds analytics tokens to the context.
    This is used for Google Analytics and other tracking services.
    """
    analyticsContext = {
        'analytics': {
            'tokens': {
                'mixpanel_project_token': settings.MIXPANEL_PROJECT_TOKEN,
            },
            "events": _enum_to_dict(AnalyticsEvent),
            "cta": _enum_to_dict(AnalyticsCTAs),
            "data": {
                "email_hash": (
                    sha256_hex(request.user.email)
                    if request.user.is_authenticated
                    else request.session.get('signup_email_hash', "")
                ),
                "id_hash": (
                    sha256_hex(str(request.user.id))
                    if request.user.is_authenticated
                    else ""
                ),
                "ip": Analytics.get_client_ip(request),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        }
    }

    return analyticsContext


def llm_bootstrap(request):
    """Expose whether the platform still requires initial LLM configuration."""
    return {
        'llm_bootstrap_required': is_llm_bootstrap_required()
    }


def canonical_url(request: HttpRequest):
    """Provide a canonical URL for templates; avoid query strings by using request.path."""
    canonical = ""
    if hasattr(request, "build_absolute_uri"):
        canonical = request.build_absolute_uri(getattr(request, "path", "/"))
    return {
        'canonical_url': canonical,
    }
