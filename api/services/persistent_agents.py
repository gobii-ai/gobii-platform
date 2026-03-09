import logging
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.crypto import get_random_string

from agent_namer import AgentNameGenerator
from agents.services import PretrainedWorkerTemplateService, AgentService

from api.agent.core.llm_config import resolve_intelligence_tier_for_owner
from api.agent.avatar import maybe_schedule_agent_avatar
from api.agent.short_description import (
    maybe_schedule_mini_description,
    maybe_schedule_short_description,
)
from api.agent.tags import maybe_schedule_agent_tags
from api.models import (
    BrowserUseAgent,
    CommsChannel,
    IntelligenceTier,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
)
from api.services.daily_credit_limits import (
    calculate_daily_credit_slider_bounds,
    get_tier_credit_multiplier,
)
from api.services.daily_credit_settings import get_daily_credit_settings_for_owner
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
        preferred_llm_tier: IntelligenceTier | None = None,
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

            owner = organization or user
            preferred_key = getattr(preferred_llm_tier, "key", None) if preferred_llm_tier is not None else None
            try:
                computed_tier = resolve_intelligence_tier_for_owner(owner, preferred_key)
            except ValueError:
                raise PersistentAgentProvisioningError("Unsupported intelligence tier selection.")

            persistent_agent = PersistentAgent(
                user=user,
                organization=organization,
                name=agent_name,
                charter=charter or "",
                schedule=schedule,
                browser_use_agent=browser_agent,
                is_active=is_active,
                preferred_contact_endpoint=preferred_contact_endpoint,
                preferred_llm_tier=computed_tier,
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

            # Apply plan-specific default daily credit limits
            if settings.GOBII_PROPRIETARY_MODE:
                owner = organization or user
                plan_value = getattr(getattr(owner, "billing", None), "subscription", PlanNamesChoices.FREE)

                try:
                    plan_choice = PlanNamesChoices(plan_value)
                except ValueError:
                    plan_choice = PlanNamesChoices.FREE

                plan_default_targets = {
                    PlanNamesChoices.FREE: settings.DEFAULT_AGENT_DAILY_CREDIT_TARGET,
                    PlanNamesChoices.STARTUP: settings.PAID_AGENT_DAILY_CREDIT_TARGET,
                    PlanNamesChoices.SCALE: settings.PAID_AGENT_DAILY_CREDIT_TARGET,
                    PlanNamesChoices.ORG_TEAM: settings.PAID_AGENT_DAILY_CREDIT_TARGET,
                }

                soft_target_value = plan_default_targets.get(plan_choice)
                if soft_target_value is not None:
                    soft_target_default = Decimal(str(soft_target_value))
                    if soft_target_default <= Decimal("0"):
                        persistent_agent.daily_credit_limit = int(soft_target_default)
                        persistent_agent.save(update_fields=["daily_credit_limit"])
                    else:
                        tier_multiplier = get_tier_credit_multiplier(computed_tier)
                        credit_settings = get_daily_credit_settings_for_owner(owner)
                        slider_bounds = calculate_daily_credit_slider_bounds(
                            credit_settings,
                            tier_multiplier=tier_multiplier,
                        )
                        scaled = (soft_target_default * tier_multiplier).to_integral_value(
                            rounding=ROUND_HALF_UP
                        )
                        if scaled < slider_bounds["slider_min"]:
                            scaled = slider_bounds["slider_min"]
                        if scaled > slider_bounds["slider_limit_max"]:
                            scaled = slider_bounds["slider_limit_max"]
                        persistent_agent.daily_credit_limit = int(scaled)
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
                    maybe_schedule_mini_description(persistent_agent)
                except Exception:
                    logger.exception(
                        "Failed to schedule mini description generation during provisioning for agent %s",
                        persistent_agent.id,
                    )
                try:
                    maybe_schedule_agent_tags(persistent_agent)
                except Exception:
                    logger.exception(
                        "Failed to schedule tag generation during provisioning for agent %s",
                        persistent_agent.id,
                    )
                try:
                    maybe_schedule_agent_avatar(persistent_agent)
                except Exception:
                    logger.exception(
                        "Failed to schedule avatar generation during provisioning for agent %s",
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


def generate_unique_agent_email(agent_name: str, max_attempts: int = 100) -> str:
    """Generate a unique default email address for an agent."""
    base_username = (agent_name or "").lower().strip()
    base_username = re.sub(r"\s+", ".", base_username)
    base_username = re.sub(r"[^\w.]", "", base_username)
    if not base_username:
        base_username = "agent"

    domain = getattr(settings, "DEFAULT_AGENT_EMAIL_DOMAIN", "agents.localhost")
    email_address = f"{base_username}@{domain}"
    if not PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.EMAIL,
        address__iexact=email_address,
    ).exists():
        return email_address

    for index in range(2, max_attempts):
        email_address = f"{base_username}{index}@{domain}"
        if not PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.EMAIL,
            address__iexact=email_address,
        ).exists():
            return email_address

    random_suffix = get_random_string(4).lower()
    email_address = f"{base_username}-{random_suffix}@{domain}"
    if not PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.EMAIL,
        address__iexact=email_address,
    ).exists():
        return email_address

    raise PersistentAgentProvisioningError("Unable to generate a unique email address for the agent.")


def ensure_default_agent_email_endpoint(
    agent: PersistentAgent,
    *,
    is_primary: bool = False,
) -> PersistentAgentCommsEndpoint | None:
    """
    Ensure an agent-owned email endpoint exists when default agent email is enabled.

    Returns the existing or created endpoint, or None when default agent email is disabled.
    """
    existing_endpoint = (
        agent.comms_endpoints.filter(channel=CommsChannel.EMAIL)
        .order_by("-is_primary", "address")
        .first()
    )
    if existing_endpoint:
        endpoint_updates: list[str] = []
        if existing_endpoint.owner_agent_id != agent.id:
            existing_endpoint.owner_agent = agent
            endpoint_updates.append("owner_agent")
        if is_primary and not existing_endpoint.is_primary:
            existing_endpoint.is_primary = True
            endpoint_updates.append("is_primary")
        if endpoint_updates:
            existing_endpoint.save(update_fields=endpoint_updates)
        if is_primary:
            agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, is_primary=True).exclude(
                id=existing_endpoint.id
            ).update(is_primary=False)
        PersistentAgentEmailEndpoint.objects.get_or_create(
            endpoint=existing_endpoint,
            defaults={
                "display_name": (agent.name or "").strip() or "Agent",
                "verified": True,
            },
        )
        return existing_endpoint

    if not settings.ENABLE_DEFAULT_AGENT_EMAIL:
        return None

    display_name = (agent.name or "").strip() or "Agent"
    for _ in range(3):
        email_address = generate_unique_agent_email(display_name)
        try:
            endpoint = PersistentAgentCommsEndpoint.objects.create(
                owner_agent=agent,
                channel=CommsChannel.EMAIL,
                address=email_address,
                is_primary=is_primary,
            )
        except IntegrityError:
            # Address races are rare; retry with a fresh generated value.
            continue

        PersistentAgentEmailEndpoint.objects.create(
            endpoint=endpoint,
            display_name=display_name,
            verified=True,
        )
        return endpoint

    raise PersistentAgentProvisioningError("Unable to provision an email endpoint for the agent.")


def maybe_sync_agent_email_display_name(agent: PersistentAgent, previous_name: str | None = None) -> bool:
    """Update the agent email display name if it matches the previous name or is blank."""
    if agent is None:
        return False
    desired_name = (agent.name or "").strip()
    if not desired_name:
        return False

    endpoint = (
        agent.comms_endpoints.filter(channel=CommsChannel.EMAIL)
        .order_by("-is_primary")
        .first()
    )
    if not endpoint:
        return False

    try:
        email_meta = endpoint.email_meta
    except PersistentAgentEmailEndpoint.DoesNotExist:
        return False

    current_display = (email_meta.display_name or "").strip()
    previous_display = (previous_name or "").strip()

    if current_display:
        if not previous_display:
            return False
        if current_display.lower() != previous_display.lower():
            return False

    if current_display == desired_name:
        return False

    email_meta.display_name = desired_name
    email_meta.save(update_fields=["display_name"])
    return True
