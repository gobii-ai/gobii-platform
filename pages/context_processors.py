import hashlib
from hashlib import sha256

from agents.services import AgentService
from api.agent.core.llm_config import is_llm_bootstrap_required
from config import settings
from config.plans import AGENTS_UNLIMITED
from constants.plans import PlanNames
from tasks.services import TaskCreditService
from util.analytics import AnalyticsEvent, AnalyticsCTAs
from util.constants.task_constants import TASKS_UNLIMITED
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

def sha256_hex(value: str | None) -> str:
    """
    Lower-case, trim, encode UTF-8, then return hex digest.
    Empty string if value is None / blank.
    """
    if not value:
        return ""
    normalised = value.strip().lower().encode("utf-8")
    return hashlib.sha256(normalised).hexdigest()


def account_info(request):
    """
    Adds account info to every template so you can write
        {% if account.has_free_agent_slots %} … {% endif %}
    """
    if not request.user.is_authenticated:
        return {}                         # skip work for anonymous users

    # Get the user's plan and subscription details
    plan = get_user_plan(request.user)
    agents_unlimited = has_unlimited_agents(request.user) or ()

    paid_plan = plan['id'] != PlanNames.FREE

    # Get the user's task credits - there are multiple calls below that we can recycle this in to save on DB calls
    task_credits = TaskCreditService.get_current_task_credit(request.user)
    tasks_available = TaskCreditService.get_user_task_credits_available(request.user, task_credits=task_credits)
    max_task_cost = get_most_expensive_tool_cost()

    # Determine if the user effectively has unlimited tasks (e.g., unlimited additional tasks)
    tasks_entitled = TaskCreditService.get_tasks_entitled(request.user)
    tasks_unlimited = tasks_entitled == TASKS_UNLIMITED

    acct_info = {
        'account': {
            'plan': plan,
            'paid': paid_plan,
            'usage': {
                'rate_limit': get_user_api_rate_limit(request.user),
                'agent_limit': get_user_agent_limit(request.user),
                'agents_unlimited': agents_unlimited,
                'agents_in_use': AgentService.get_agents_in_use(request.user),
                'agents_available': AGENTS_UNLIMITED if agents_unlimited is True else AgentService.get_agents_available(request.user),
                'tasks_entitled': tasks_entitled,
                'tasks_available': tasks_available,
                # If unlimited, usage is effectively 0%; else treat "can't afford a single tool" as 100%
                'tasks_used_pct': (
                    0 if (tasks_unlimited or tasks_available == TASKS_UNLIMITED) else (
                        100 if tasks_available < max_task_cost else TaskCreditService.get_user_task_credits_used_pct(request.user, task_credits=task_credits)
                    )
                ),
                'tasks_addl_enabled': allow_user_extra_tasks(request.user),
                'tasks_addl_limit': get_user_extra_task_limit(request.user),
                'task_credits_monthly': get_user_task_credit_limit(request.user),
                'task_credits_available': TaskCreditService.calculate_available_tasks(request.user, task_credits=task_credits),
                'max_contacts_per_agent': get_user_max_contacts_per_agent(request.user),
            }
        }
    }

    return acct_info

def environment_info(request):
    """
    Adds environment info to every template so you can write
        {% if environment.is_production %} … {% endif %}
    """
    return {
        'environment': {
            'is_production': settings.GOBII_RELEASE_ENV == 'prod',
        }
    }


def show_signup_tracking(request):
    """
    Adds a flag to the context to control whether to show signup tracking.
    This is set in the user_signed_up signal handler.
    """
    return {
        'show_signup_tracking': request.session.get('show_signup_tracking', False)
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
                "email_hash": sha256_hex(request.user.email) if request.user.is_authenticated else "",
            }
        }
    }

    return analyticsContext


def llm_bootstrap(request):
    """Expose whether the platform still requires initial LLM configuration."""
    return {
        'llm_bootstrap_required': is_llm_bootstrap_required()
    }
