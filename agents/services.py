import copy
import logging
import random
from typing import Dict, Iterable, Sequence

from django.apps import apps

from agents.template_definitions import (
    TEMPLATE_DEFINITIONS,
    AIEmployeeTemplateDefinition,
)
from config.plans import AGENTS_UNLIMITED, MAX_AGENT_LIMIT
from observability import trace

from cron_descriptor import get_description, Options
from cron_descriptor.Exception import FormatError

from util.subscription_helper import has_unlimited_agents, is_community_unlimited_mode

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

        community_unlimited = is_community_unlimited_mode()
        plan_unlimited = has_unlimited_agents(user)

        # Step 1: Determine the user's plan/quota limit.
        # Prefer explicit per-user quota when present; fall back to plan-based checks.
        UserQuota = apps.get_model("api", "UserQuota")

        if community_unlimited or plan_unlimited:
            user_limit = MAX_AGENT_LIMIT
        else:
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
    _CRON_MACRO_MAP = {
        "@yearly": "0 0 1 1 *",
        "@annually": "0 0 1 1 *",
        "@monthly": "0 0 1 * *",
        "@weekly": "0 0 * * 0",
        "@daily": "0 0 * * *",
        "@midnight": "0 0 * * *",
        "@hourly": "0 * * * *",
    }

    @staticmethod
    def _all_templates() -> list[AIEmployeeTemplateDefinition]:
        """Return a fresh copy of all template definitions."""
        return [copy.deepcopy(template) for template in TEMPLATE_DEFINITIONS]

    @classmethod
    def get_active_templates(cls) -> list[AIEmployeeTemplateDefinition]:
        templates = [
            template for template in cls._all_templates() if getattr(template, "is_active", True)
        ]
        templates.sort(key=lambda template: (template.priority, template.display_name.lower()))
        return templates

    @classmethod
    def get_template_by_code(cls, code: str):
        if not code:
            return None
        for template in cls._all_templates():
            if template.code == code and getattr(template, "is_active", True):
                return template
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

    @staticmethod
    def describe_schedule(base_schedule: str | None) -> str | None:
        """Return a human readable description of a cron schedule."""
        if not base_schedule:
            return None

        expression = AIEmployeeTemplateService._normalize_cron_expression(base_schedule)
        if not expression:
            return base_schedule

        options = Options()
        options.verbose = True

        try:
            return get_description(expression, options)
        except FormatError:
            logger.warning("Unable to parse cron expression for description: %s", base_schedule)
        except Exception:  # pragma: no cover - defensive logging only
            logger.exception("Unexpected error while describing cron expression: %s", base_schedule)

        return base_schedule

    @staticmethod
    def _normalize_cron_expression(expression: str) -> str | None:
        expression = (expression or "").strip()
        if not expression:
            return None

        if expression.startswith("@"):
            macro = expression.lower()
            return AIEmployeeTemplateService._CRON_MACRO_MAP.get(macro)

        return expression

    @staticmethod
    def _fallback_tool_display(tool_name: str) -> str:
        cleaned = (tool_name or "").replace("_", " ").replace("-", " ").strip()
        if not cleaned:
            return tool_name
        return " ".join(part.capitalize() for part in cleaned.split())

    @staticmethod
    def get_tool_display_map(tool_names: Iterable[str]) -> Dict[str, str]:
        tool_list = [name for name in tool_names if name]
        if not tool_list:
            return {}

        ToolName = apps.get_model("api", "ToolFriendlyName")
        entries = ToolName.objects.filter(tool_name__in=tool_list)
        return {entry.tool_name: entry.display_name for entry in entries}

    @classmethod
    def get_tool_display_list(
        cls,
        tool_names: Sequence[str] | None,
        display_map: Dict[str, str] | None = None,
    ) -> list[str]:
        if not tool_names:
            return []

        display_map = display_map or cls.get_tool_display_map(tool_names)
        return [display_map.get(name, cls._fallback_tool_display(name)) for name in tool_names]

    @staticmethod
    def describe_contact_channel(channel: str | None) -> str:
        mapping = {
            "email": "Email updates",
            "sms": "Text message",
            "slack": "Slack message",
            "pagerduty": "PagerDuty alert",
        }

        if not channel:
            return mapping["email"]

        normalized = channel.lower()
        if normalized in mapping:
            label = mapping[normalized]
            if normalized == "sms":
                return f"{label} (SMS)"
            return label

        return channel.replace("_", " ").upper() if normalized == "voice" else channel.replace("_", " ").title()
