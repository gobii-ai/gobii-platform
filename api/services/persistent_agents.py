import logging
from dataclasses import dataclass
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from agent_namer import AgentNameGenerator
from agents.services import PretrainedWorkerTemplateService, AgentService

from api.agent.short_description import maybe_schedule_short_description
from api.agent.tags import maybe_schedule_agent_tags
from api.models import BrowserUseAgent, PersistentAgent
from config import settings
from constants.plans import PlanNamesChoices


logger = logging.getLogger(__name__)


class PersistentAgentProvisioningError(Exception):
    """Raised when a persistent agent cannot be provisioned."""


@dataclass(slots=True)
class ProvisioningResult:
    agent: PersistentAgent
    browser_agent: BrowserUseAgent
    applied_template_code: Optional[str] = None
    applied_schedule: Optional[str] = None


class PersistentAgentProvisioningService:
    """Utilities for creating persistent agents from API or console flows."""

    DEFAULT_MAX_NAME_ATTEMPTS = 10
    NAME_ERROR_KEY = "name"

    @classmethod
    def generate_unique_name(cls, user, *, max_attempts: int | None = None) -> str:
        """Return a unique agent name for the given user."""
        attempts = int(max_attempts or cls.DEFAULT_MAX_NAME_ATTEMPTS)
        for _ in range(attempts):
            candidate = AgentNameGenerator.generate()
            if not BrowserUseAgent.objects.filter(user=user, name=candidate).exists():
                return candidate

        base_candidate = AgentNameGenerator.generate()
        suffix = 1
        while BrowserUseAgent.objects.filter(user=user, name=f"{base_candidate} {suffix}").exists():
            suffix += 1
            if suffix > 100:
                raise PersistentAgentProvisioningError("Unable to generate a unique agent name after extensive attempts.")
        return f"{base_candidate} {suffix}"

    @classmethod
    def provision(
        cls,
        *,
        user,
        organization=None,
        name: Optional[str] = None,
        charter: str | None = "",
        schedule: str | None = None,
        is_active: bool = True,
        life_state: str | None = None,
        whitelist_policy: str | None = None,
        preferred_contact_endpoint=None,
        template_code: str | None = None,
    ) -> ProvisioningResult:
        """Create a new persistent agent and its backing browser agent."""
        agent_name = name or cls.generate_unique_name(user)

        # Ensure the owner has capacity before we hit database constraints — the
        # BrowserUseAgent clean() method enforces this but we prefer an early,
        # explicit error for API consumers.
        owner = organization or user
        if not AgentService.has_agents_available(owner):
            raise PersistentAgentProvisioningError("Agent limit reached for this user.")

        applied_template_code: Optional[str] = None
        applied_schedule: Optional[str] = None

        with transaction.atomic():
            browser_agent = BrowserUseAgent(user=user, name=agent_name)
            if organization is not None:
                browser_agent._agent_creation_organization = organization
            try:
                browser_agent.full_clean()
                browser_agent.save()
            except ValidationError as exc:
                raise PersistentAgentProvisioningError(
                    cls._normalize_validation_error(exc)
                ) from exc
            except IntegrityError as exc:
                raise PersistentAgentProvisioningError(
                    {"name": ["An agent with this name already exists for the owner."]}
                ) from exc
            finally:
                if hasattr(browser_agent, "_agent_creation_organization"):
                    delattr(browser_agent, "_agent_creation_organization")

            persistent_agent = PersistentAgent(
                user=user,
                organization=organization,
                name=agent_name,
                charter=charter or "",
                schedule=schedule,
                browser_use_agent=browser_agent,
                is_active=is_active,
                preferred_contact_endpoint=preferred_contact_endpoint,
            )

            if life_state:
                persistent_agent.life_state = life_state
            if whitelist_policy:
                persistent_agent.whitelist_policy = whitelist_policy

            try:
                persistent_agent.full_clean()
            except ValidationError as exc:
                # Roll back browser agent if persistent agent validation fails.
                raise PersistentAgentProvisioningError(
                    cls._normalize_validation_error(exc)
                ) from exc

            persistent_agent.save()

            # Default daily credit limit for free plans
            if settings.GOBII_PROPRIETARY_MODE:
                owner = organization or user
                plan_value = getattr(getattr(owner, "billing", None), "subscription", PlanNamesChoices.FREE)

                try:
                    plan_choice = PlanNamesChoices(plan_value)
                except ValueError:
                    plan_choice = PlanNamesChoices.FREE

                if plan_choice == PlanNamesChoices.FREE:
                    persistent_agent.daily_credit_limit = settings.DEFAULT_AGENT_DAILY_CREDIT_LIMIT
                    persistent_agent.save(update_fields=["daily_credit_limit"])

            if template_code:
                template = PretrainedWorkerTemplateService.get_template_by_code(template_code)
                if template is None:
                    raise PersistentAgentProvisioningError(f"Unknown template code '{template_code}'.")

                applied_template_code = template.code
                updates: list[str] = []

                if not charter and template.charter:
                    persistent_agent.charter = template.charter
                    updates.append("charter")

                computed = PretrainedWorkerTemplateService.compute_schedule_with_jitter(
                    template.base_schedule,
                    template.schedule_jitter_minutes,
                )
                if computed:
                    persistent_agent.schedule = computed
                    persistent_agent.schedule_snapshot = template.base_schedule
                    applied_schedule = computed
                    updates.extend(["schedule", "schedule_snapshot"])

                if updates:
                    try:
                        persistent_agent.full_clean()
                    except ValidationError as exc:
                        raise PersistentAgentProvisioningError(
                            cls._normalize_validation_error(exc)
                        ) from exc
                    persistent_agent.save(update_fields=updates)

            def _schedule_charter_artifacts() -> None:
                try:
                    maybe_schedule_short_description(persistent_agent)
                except Exception:
                    logger.exception(
                        "Failed to schedule short description generation during provisioning for agent %s",
                        persistent_agent.id,
                    )
                try:
                    maybe_schedule_agent_tags(persistent_agent)
                except Exception:
                    logger.exception(
                        "Failed to schedule tag generation during provisioning for agent %s",
                        persistent_agent.id,
                    )

            transaction.on_commit(_schedule_charter_artifacts)

            return ProvisioningResult(
                agent=persistent_agent,
                browser_agent=browser_agent,
                applied_template_code=applied_template_code,
                applied_schedule=applied_schedule,
            )

    @classmethod
    def _normalize_validation_error(cls, exc: ValidationError) -> dict | list | str:
        """Convert Django validation errors into serializer-friendly structures."""
        if hasattr(exc, "message_dict"):
            message_dict = dict(exc.message_dict)
            if "__all__" in message_dict and cls.NAME_ERROR_KEY not in message_dict:
                message_dict[cls.NAME_ERROR_KEY] = message_dict.pop("__all__")
            return message_dict
        return exc.messages
