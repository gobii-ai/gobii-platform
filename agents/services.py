import copy
import logging
import random
from typing import Dict, Iterable, Sequence, Any

from django.apps import apps
from django.contrib.auth import get_user_model

from agents.pretrained_worker_definitions import (
    TEMPLATE_DEFINITIONS,
    PretrainedWorkerTemplateDefinition,
)
from config.plans import AGENTS_UNLIMITED, MAX_AGENT_LIMIT, PLAN_CONFIG
from observability import trace

from cron_descriptor import get_description, Options
from cron_descriptor.Exception import FormatError

from util.subscription_helper import (
    get_organization_plan,
    has_unlimited_agents,
    is_community_unlimited_mode,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer('gobii.utils')

class AgentService:
    """
    AgentService is a base class for agent services.
    It provides a common interface for all agent services.
    """

    @staticmethod
    def _resolve_organization_instance(organization: Any):
        """Return a hydrated Organization instance (with billing) for plan checks."""
        if organization is None:
            return None

        Organization = apps.get_model("api", "Organization")

        try:
            if isinstance(organization, Organization):
                org_id = organization.id
            else:
                org_id = organization
            if org_id is None:
                return None
            return Organization.objects.select_related("billing").get(pk=org_id)
        except (Organization.DoesNotExist, ValueError, TypeError):
            logger.warning("AgentService: Organization %s not found when resolving context", organization)
            return None

    @staticmethod
    def _normalize_owner(owner: Any) -> tuple[str, Any, Any]:
        """Return (owner_type, owner_id, owner_instance) for a user or organization owner."""
        if owner is None:
            return "unknown", None, None

        Organization = apps.get_model("api", "Organization")
        UserModel = get_user_model()

        if isinstance(owner, Organization):
            org = AgentService._resolve_organization_instance(owner)
            return ("organization", getattr(org, "id", None), org)

        if isinstance(owner, UserModel):
            return ("user", owner.id, owner)

        # Attempt to resolve as organization primary key first.
        org = AgentService._resolve_organization_instance(owner)
        if org is not None:
            return ("organization", org.id, org)

        # Fall back to user lookup by primary key.
        try:
            user = UserModel.objects.get(pk=owner)
            return ("user", user.id, user)
        except (UserModel.DoesNotExist, ValueError, TypeError):
            logger.warning("AgentService: Unable to resolve owner %s", owner)
            return "unknown", None, None

    @staticmethod
    def _count_agents(owner_type: str, owner_id: Any) -> int:
        """Return count of persistent agents for the provided owner context."""
        if owner_type not in {"user", "organization"} or owner_id is None:
            return 0

        PersistentAgent = apps.get_model("api", "PersistentAgent")

        if owner_type == "organization":
            return PersistentAgent.objects.non_eval().filter(organization_id=owner_id).count()

        return PersistentAgent.objects.non_eval().filter(user_id=owner_id, organization__isnull=True).count()

    @staticmethod
    @tracer.start_as_current_span("AGENT SERVICE: get_agents_in_use")
    def get_agents_in_use(owner) -> int:
        """
        Returns a count of agents that are currently in use for the provided owner.

        Parameters
        ----------
        owner : User | Organization | UUID | str
            Entity that owns the agents. Can be a user instance, organization instance,
            or the primary key for either.

        Returns
        -------
        int
            Number of agents currently in use for the owner.
        """
        owner_type, owner_id, _ = AgentService._normalize_owner(owner)
        return AgentService._count_agents(owner_type, owner_id)

    @staticmethod
    @tracer.start_as_current_span("AGENT SERVICE: get_agents_available")
    def get_agents_available(owner) -> int:
        """
        Returns the number of agents available for the provided owner.

        Parameters
        ----------
        owner : User | Organization | UUID | str
            Entity that owns the agents. Can be a user instance, organization instance,
            or the primary key for either.

        Returns
        -------
        int
            Number of additional agents that may be created for the owner.
        """
        owner_type, owner_id, owner_instance = AgentService._normalize_owner(owner)
        if owner_id is None:
            return 0

        in_use = AgentService._count_agents(owner_type, owner_id)
        community_unlimited = is_community_unlimited_mode()

        if owner_type == "organization":
            organization_obj = owner_instance or AgentService._resolve_organization_instance(owner_id)
            if organization_obj is None:
                return 0

            plan = get_organization_plan(organization_obj) or PLAN_CONFIG["free"]
            plan_limit = plan.get("agent_limit", PLAN_CONFIG["free"]["agent_limit"])

            if community_unlimited or plan_limit == AGENTS_UNLIMITED:
                org_limit = MAX_AGENT_LIMIT
            else:
                org_limit = PLAN_CONFIG["org_team"]["agent_limit"]
                if org_limit == AGENTS_UNLIMITED:
                    org_limit = MAX_AGENT_LIMIT
                try:
                    org_limit = int(org_limit)
                except (TypeError, ValueError):
                    org_limit = PLAN_CONFIG["free"]["agent_limit"]
                org_limit = min(org_limit, MAX_AGENT_LIMIT)

            return max(org_limit - in_use, 0)

        # User-owned agents
        user = owner_instance
        if user is None:
            return 0

        plan_unlimited = has_unlimited_agents(user)
        UserQuota = apps.get_model("api", "UserQuota")

        if community_unlimited or plan_unlimited:
            user_limit = MAX_AGENT_LIMIT
        else:
            try:
                user_quota = UserQuota.objects.get(user_id=owner_id)
                user_limit = min(user_quota.agent_limit, MAX_AGENT_LIMIT)
            except UserQuota.DoesNotExist:
                logger.warning(f"UserQuota not found for user_id: {owner_id}")
                return 0

        return max(user_limit - in_use, 0)

    @staticmethod
    @tracer.start_as_current_span("AGENT SERVICE: has_agents_available")
    def has_agents_available(owner) -> bool:
        """
        Checks if the provided owner has agent capacity remaining.

        Parameters
        ----------
        owner : User | Organization | UUID | str
            Entity that owns the agents. Can be a user instance, organization instance,
            or the primary key for either.

        Returns
        -------
        bool
            True if additional agents can be created, False otherwise.
        """
        owner_type, _, _ = AgentService._normalize_owner(owner)
        if owner_type == "unknown":
            return False

        available = AgentService.get_agents_available(owner)
        if available > 0:
            return True
        # We always enforce the global safety cap, even for unlimited plans.
        return False


class PretrainedWorkerTemplateService:
    """Utilities for working with curated pretrained worker templates."""

    TEMPLATE_SESSION_KEY = "pretrained_worker_template_code"
    CODE_ALIASES = {
        "talent-sourcer": "talent-scout",
    }
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
    def _all_templates() -> list[PretrainedWorkerTemplateDefinition]:
        """Return a fresh copy of all pretrained worker template definitions."""
        return [copy.deepcopy(template) for template in TEMPLATE_DEFINITIONS]

    @staticmethod
    def _template_from_model(template) -> PretrainedWorkerTemplateDefinition:
        return PretrainedWorkerTemplateDefinition(
            code=template.code,
            display_name=template.display_name,
            tagline=template.tagline,
            description=template.description,
            charter=template.charter,
            base_schedule=template.base_schedule or "",
            schedule_jitter_minutes=template.schedule_jitter_minutes or 0,
            event_triggers=list(template.event_triggers or []),
            default_tools=list(template.default_tools or []),
            recommended_contact_channel=template.recommended_contact_channel or "email",
            category=template.category or "",
            hero_image_path=template.hero_image_path or "",
            priority=template.priority,
            is_active=template.is_active,
            show_on_homepage=template.show_on_homepage,
        )

    @classmethod
    def _db_templates(cls, *, include_inactive: bool = False) -> list[PretrainedWorkerTemplateDefinition]:
        Template = apps.get_model("api", "PersistentAgentTemplate")
        qs = Template.objects.filter(public_profile__isnull=True)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        qs = qs.order_by("priority", "display_name")
        return [cls._template_from_model(template) for template in qs]

    @classmethod
    def get_active_templates(cls) -> list[PretrainedWorkerTemplateDefinition]:
        db_templates = cls._db_templates()
        if db_templates:
            return db_templates

        templates = [
            template for template in cls._all_templates() if getattr(template, "is_active", True)
        ]
        templates.sort(key=lambda template: (template.priority, template.display_name.lower()))
        return templates

    @classmethod
    def get_template_by_code(cls, code: str):
        if not code:
            return None
        normalized = code.strip().lower()
        normalized = cls.CODE_ALIASES.get(normalized, normalized)
        Template = apps.get_model("api", "PersistentAgentTemplate")
        db_template = Template.objects.filter(code=normalized, is_active=True).first()
        if db_template:
            return cls._template_from_model(db_template)
        for template in cls._all_templates():
            if template.code == normalized and getattr(template, "is_active", True):
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

        expression = PretrainedWorkerTemplateService._normalize_cron_expression(base_schedule)
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
            return PretrainedWorkerTemplateService._CRON_MACRO_MAP.get(macro)

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
