from django.apps import apps

import random

from config.plans import AGENTS_UNLIMITED, MAX_AGENT_LIMIT
from observability import trace

import logging

from util.subscription_helper import has_unlimited_agents

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')

class AgentService:
    """
    AgentService is a base class for agent services.
    It provides a common interface for all agent services.
    """

    @staticmethod
    @tracer.start_as_current_span("AGENT SERVICE: get_agents_in_use")
    def get_agents_in_use(user) -> int:
        """
        Returns a count of agents that are currently in use by the user.

        Parameters:
        ----------
        user : User
            The user whose agents are to be checked.

        Returns:
        -------
        int
            A count of agents that are currently in use by the user.
        """
        BrowserUseAgent = apps.get_model("api", "BrowserUseAgent")
        return BrowserUseAgent.objects.filter(user_id=user.id).count()

    @staticmethod
    @tracer.start_as_current_span("AGENT SERVICE: get_agents_available")
    def get_agents_available(user) -> int:
        """
        Returns the number of agents available for the user.

        Parameters:
        ----------
        user : User
            The user whose agent availability is to be checked.

        Returns:
        -------
        int
            The number of agents available for the user.
        """
        """
        We always enforce an absolute safety cap of ``MAX_AGENT_LIMIT`` even for
        "unlimited" plans.  This prevents runaway usage scenarios while we are
        still scaling the infrastructure.

        Implementation details:
        1. Users on an unlimited plan get a ceiling of ``MAX_AGENT_LIMIT``.
        2. Users with a smaller per-plan or per-quota limit keep that lower
           value.
        3. The return value is **never** negative – when the user has reached or
           exceeded their limit we return ``0`` so callers can fail fast.
        """

        in_use = AgentService.get_agents_in_use(user)

        # Step 1: Determine the user's plan/quota limit.
        # Prefer explicit per-user quota when present; fall back to plan-based checks.
        UserQuota = apps.get_model("api", "UserQuota")
        try:
            user_quota = UserQuota.objects.get(user_id=user.id)
            user_limit = min(user_quota.agent_limit, MAX_AGENT_LIMIT)
        except UserQuota.DoesNotExist:
            # Without an explicit per-user quota, treat as no capacity.
            # Tests and safety expectations prefer an explicit quota to be present.
            logger.warning(f"UserQuota not found for user_id: {user.id}")
            return 0

        # Step 2: Calculate remaining slots (never negative).
        remaining = max(user_limit - in_use, 0)

        return remaining

    @staticmethod
    @tracer.start_as_current_span("AGENT SERVICE: has_agents_available")
    def has_agents_available(user) -> bool:
        """
        Checks if the user has any agents available.

        Parameters:
        ----------
        user : User
            The user whose agent availability is to be checked.

        Returns:
        -------
        bool
            True if the user has agents available, False otherwise.
        """
        # -1 is unlimited, so we just check if not 0
        return AgentService.get_agents_available(user) > 0 or has_unlimited_agents(user)


class AIEmployeeTemplateService:
    """Utilities for working with curated AI employee templates."""

    TEMPLATE_SESSION_KEY = "ai_employee_template_code"

    @staticmethod
    def get_active_templates():
        Template = apps.get_model("api", "PersistentAgentTemplate")
        return Template.objects.filter(is_active=True).order_by("priority", "display_name")

    @staticmethod
    def get_template_by_code(code: str):
        if not code:
            return None
        Template = apps.get_model("api", "PersistentAgentTemplate")
        try:
            return Template.objects.get(code=code, is_active=True)
        except Template.DoesNotExist:
            return None

    @staticmethod
    def compute_schedule_with_jitter(base_schedule: str | None, jitter_minutes: int | None) -> str | None:
        """Return a cron schedule string with jitter applied to minutes/hours."""
        if not base_schedule:
            return None

        jitter = max(int(jitter_minutes or 0), 0)
        if jitter == 0:
            return base_schedule

        if base_schedule.startswith("@"):
            # Unsupported shortcut format – best effort by returning original.
            return base_schedule

        parts = base_schedule.split()
        if len(parts) != 5:
            return base_schedule

        minute, hour, day_of_month, month, day_of_week = parts

        if not (minute.isdigit() and hour.isdigit()):
            return base_schedule

        minute_val = int(minute)
        hour_val = int(hour)

        total_minutes = hour_val * 60 + minute_val
        offset = random.randint(-jitter, jitter)
        total_minutes = (total_minutes + offset) % (24 * 60)

        jittered_hour, jittered_minute = divmod(total_minutes, 60)

        return f"{jittered_minute} {jittered_hour} {day_of_month} {month} {day_of_week}"
