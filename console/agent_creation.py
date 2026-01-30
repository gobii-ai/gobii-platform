from dataclasses import dataclass
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.contrib import messages
from django.urls import reverse
from django.utils.html import format_html

from agents.services import PretrainedWorkerTemplateService
from api.agent.comms.message_service import _ensure_participant, _get_or_create_conversation
from api.agent.tasks import process_agent_events_task
from api.agent.tools.tool_manager import mark_tool_enabled_without_discovery
from api.agent.core.llm_config import AgentLLMTier, TIER_ORDER, max_allowed_tier_for_plan
from api.models import (
    CommsChannel,
    IntelligenceTier,
    Organization,
    PersistentAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentConversationParticipant,
    PersistentAgentEmailEndpoint,
    PersistentAgentMessage,
    PersistentAgentSmsEndpoint,
)
from api.services.persistent_agents import PersistentAgentProvisioningError, PersistentAgentProvisioningService
from config import settings
from console.context_helpers import build_console_context
from util import sms
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.sms import find_unused_number, get_user_primary_sms_number
from util.subscription_helper import get_owner_plan

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentCreationResult:
    agent: PersistentAgent
    organization: Organization | None
    applied_schedule: str | None
    template_code: str | None
    contact_email: str | None
    contact_sms: str | None
    preferred_contact_method: str
    initial_message: str


def generate_unique_agent_email(agent_name: str, max_attempts: int = 100) -> str:
    import re
    from django.utils.crypto import get_random_string

    base_username = agent_name.lower().strip()
    base_username = re.sub(r"\s+", ".", base_username)
    base_username = re.sub(r"[^\w.]", "", base_username)
    domain = getattr(settings, "DEFAULT_AGENT_EMAIL_DOMAIN", "agents.localhost")

    email_address = f"{base_username}@{domain}"
    if not PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.EMAIL, address__iexact=email_address
    ).exists():
        return email_address

    for i in range(2, max_attempts):
        email_address = f"{base_username}{i}@{domain}"
        if not PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.EMAIL, address__iexact=email_address
        ).exists():
            return email_address

    random_suffix = get_random_string(4)
    email_address = f"{base_username}-{random_suffix}@{domain}"
    if not PersistentAgentCommsEndpoint.objects.filter(
        channel=CommsChannel.EMAIL, address__iexact=email_address
    ).exists():
        return email_address

    raise ValueError("Unable to generate a unique email address for the agent.")


def create_persistent_agent_from_charter(
    request: HttpRequest,
    *,
    initial_message: str,
    contact_email: str | None,
    email_enabled: bool,
    sms_enabled: bool,
    preferred_contact_method: str,
    preferred_llm_tier_key: str | None = None,
) -> AgentCreationResult:
    initial_message = (initial_message or "").strip()
    if not initial_message:
        raise ValidationError("Please start by describing what your agent should do.")

    preferred_contact_method = (preferred_contact_method or "email").strip().lower()
    contact_email = (contact_email or "").strip()

    template_code = request.session.get(PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY)
    selected_template = PretrainedWorkerTemplateService.get_template_by_code(template_code) if template_code else None

    resolved_context = build_console_context(request)
    organization = None
    if resolved_context.current_context.type == "organization":
        membership = resolved_context.current_membership
        if membership is None:
            messages.error(
                request,
                "You no longer have access to that organization. Creating a personal agent instead.",
            )
        elif not resolved_context.can_manage_org_agents:
            raise ValidationError(
                "You need to be an organization owner or admin to create agents for this organization."
            )
        else:
            organization = membership.org

        billing = getattr(organization, "billing", None)
        seats_purchased = getattr(billing, "purchased_seats", 0) if billing else 0
        if seats_purchased <= 0:
            billing_url = f"{reverse('billing')}?org_id={organization.id}"
            request.session["context_type"] = "organization"
            request.session["context_id"] = str(organization.id)
            request.session["context_name"] = organization.name
            request.session.modified = True

            message_text = format_html(
                "Looks like your organization doesn't have any seats yet. "
                "<a class=\"underline font-medium\" href=\"{}\">Add seats in Billing</a> "
                "to create organization-owned agents.",
                billing_url,
            )
            raise ValidationError(message_text)

    if email_enabled and not contact_email:
        raise ValidationError("Please provide an email address for agent contact.")

    user_contact_email = contact_email if email_enabled else None
    user_contact_sms = None
    sms_preferred = preferred_contact_method == "sms"

    preferred_llm_tier = None
    if preferred_llm_tier_key:
        tier_key = preferred_llm_tier_key.strip().lower()
        try:
            tier = AgentLLMTier(tier_key)
        except ValueError as exc:
            raise ValidationError("Unsupported intelligence tier selection.") from exc

        if settings.GOBII_PROPRIETARY_MODE:
            owner = organization or request.user
            plan = get_owner_plan(owner) if owner else None
            allowed = max_allowed_tier_for_plan(plan, is_organization=organization is not None)
            if TIER_ORDER[tier] > TIER_ORDER[allowed]:
                raise ValidationError("Upgrade your plan to choose this intelligence tier.")

        preferred_llm_tier = IntelligenceTier.objects.filter(key=tier.value).first()
        if preferred_llm_tier is None:
            raise ValidationError("Unsupported intelligence tier selection.")

    with transaction.atomic():
        try:
            provisioning = PersistentAgentProvisioningService.provision(
                user=request.user,
                organization=organization,
                template_code=template_code,
                charter=initial_message,
                preferred_llm_tier=preferred_llm_tier,
            )
        except PersistentAgentProvisioningError as exc:
            error_payload = exc.args[0] if exc.args else "Unable to create agent."
            raise ValidationError(error_payload) from exc

        persistent_agent = provisioning.agent
        applied_schedule = provisioning.applied_schedule
        agent_name = persistent_agent.name

        user_sms_comms_endpoint = None
        user_email_comms_endpoint = None
        agent_sms_endpoint = None
        agent_email_endpoint = None

        if sms_enabled:
            user_primary_sms = get_user_primary_sms_number(user=request.user)
            user_contact_sms = user_primary_sms.phone_number if user_primary_sms else None

            if user_primary_sms is None:
                raise ValidationError(
                    "You must have a verified phone number to create an agent with SMS contact."
                )

            agent_sms = find_unused_number()

            agent_sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
                owner_agent=persistent_agent,
                channel=CommsChannel.SMS,
                address=agent_sms.phone_number,
                is_primary=sms_preferred,
            )
            PersistentAgentSmsEndpoint.objects.create(
                endpoint=agent_sms_endpoint,
                supports_mms=True,
                carrier_name=agent_sms.provider,
            )

            user_sms_comms_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.SMS,
                address=user_primary_sms.phone_number,
                defaults={"owner_agent": None},
            )

        if email_enabled:
            if getattr(settings, "ENABLE_DEFAULT_AGENT_EMAIL", False):
                agent_email = generate_unique_agent_email(agent_name)
                agent_email_endpoint = PersistentAgentCommsEndpoint.objects.create(
                    owner_agent=persistent_agent,
                    channel=CommsChannel.EMAIL,
                    address=agent_email,
                    is_primary=preferred_contact_method == "email",
                )
                PersistentAgentEmailEndpoint.objects.create(
                    endpoint=agent_email_endpoint,
                    display_name=agent_name,
                    verified=True,
                )

            user_email_comms_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                channel=CommsChannel.EMAIL,
                address=user_contact_email,
                defaults={"owner_agent": None},
            )

        if sms_preferred:
            preferred_endpoint = user_sms_comms_endpoint
            user_contact = user_contact_sms
        else:
            preferred_endpoint = user_email_comms_endpoint
            user_contact = user_contact_email

        if preferred_endpoint is None or not user_contact:
            raise ValidationError("We could not determine your preferred contact channel.")

        persistent_agent.preferred_contact_endpoint = preferred_endpoint
        persistent_agent.save(update_fields=["preferred_contact_endpoint"])

        if sms_enabled and user_contact_sms and agent_sms_endpoint:
            try:
                sms.send_sms(
                    to_number=user_contact_sms,
                    from_number=agent_sms_endpoint.address,
                    body=(
                        "Gobii: You've enabled SMS communication with Gobii. "
                        "Reply HELP for help, STOP to opt-out."
                    ),
                )
            except Exception:
                pass

        conversation = _get_or_create_conversation(
            channel=CommsChannel.SMS.value if sms_preferred else CommsChannel.EMAIL.value,
            address=user_contact,
            owner_agent=persistent_agent,
        )

        if user_sms_comms_endpoint:
            _ensure_participant(
                conversation,
                user_sms_comms_endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
            )
        if user_email_comms_endpoint:
            _ensure_participant(
                conversation,
                user_email_comms_endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
            )
        agent_channel_endpoint = agent_sms_endpoint if sms_preferred else agent_email_endpoint
        if agent_channel_endpoint is not None:
            _ensure_participant(
                conversation,
                agent_channel_endpoint,
                PersistentAgentConversationParticipant.ParticipantRole.AGENT,
            )

        PersistentAgentMessage.objects.create(
            is_outbound=False,
            from_endpoint=preferred_endpoint,
            conversation=conversation,
            body=initial_message,
            owner_agent=persistent_agent,
        )

        if selected_template and selected_template.default_tools:
            for tool_name in selected_template.default_tools:
                try:
                    mark_tool_enabled_without_discovery(persistent_agent, tool_name)
                except Exception as exc:
                    logger.warning(
                        "Failed to enable MCP tool '%s' for agent %s: %s",
                        tool_name,
                        persistent_agent.id,
                        exc,
                    )

        transaction.on_commit(lambda: process_agent_events_task.delay(str(persistent_agent.id)))

        for key in ("agent_charter", "agent_charter_source", PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY):
            if key in request.session:
                del request.session[key]

        base_props = {
            "agent_id": str(persistent_agent.id),
            "agent_name": agent_name,
            "contact_email": user_contact_email or "",
            "contact_sms": user_contact_sms or "",
            "initial_message": initial_message,
            "charter": initial_message or "",
            "preferred_contact_method": preferred_contact_method,
            "template_code": selected_template.code if selected_template else "",
            "template_schedule_applied": applied_schedule or "",
        }
        props = Analytics.with_org_properties(base_props, organization=organization)
        transaction.on_commit(
            lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_CREATED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            )
        )
        if props.get("organization"):
            transaction.on_commit(
                lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.ORGANIZATION_PERSISTENT_AGENT_CREATED,
                    source=AnalyticsSource.WEB,
                    properties=props.copy(),
                )
            )
            transaction.on_commit(
                lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.ORGANIZATION_AGENT_CREATED,
                    source=AnalyticsSource.WEB,
                    properties=props.copy(),
                )
            )

        return AgentCreationResult(
            agent=persistent_agent,
            organization=organization,
            applied_schedule=applied_schedule,
            template_code=template_code,
            contact_email=user_contact_email,
            contact_sms=user_contact_sms,
            preferred_contact_method=preferred_contact_method,
            initial_message=initial_message,
        )


def enable_agent_sms_contact(agent: PersistentAgent, phone) -> tuple[PersistentAgentCommsEndpoint, PersistentAgentCommsEndpoint]:
    if not phone or not phone.is_verified:
        raise ValidationError("Please verify a phone number before enabling SMS.")

    with transaction.atomic():
        existing_agent_sms = agent.comms_endpoints.filter(channel=CommsChannel.SMS).first()
        created = False

        if existing_agent_sms:
            agent_sms_endpoint = existing_agent_sms
            updates = []
            if not agent_sms_endpoint.is_primary:
                agent_sms_endpoint.is_primary = True
                updates.append("is_primary")
            if agent_sms_endpoint.owner_agent_id != agent.id:
                agent_sms_endpoint.owner_agent = agent
                updates.append("owner_agent")
            if updates:
                agent_sms_endpoint.save(update_fields=updates)
        else:
            agent_sms = find_unused_number()
            agent_sms_endpoint = PersistentAgentCommsEndpoint.objects.create(
                owner_agent=agent,
                channel=CommsChannel.SMS,
                address=agent_sms.phone_number,
                is_primary=True,
            )
            PersistentAgentSmsEndpoint.objects.create(
                endpoint=agent_sms_endpoint,
                supports_mms=True,
                carrier_name=agent_sms.provider,
            )
            created = True

        user_sms_endpoint, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
            channel=CommsChannel.SMS,
            address=phone.phone_number,
            defaults={"owner_agent": None},
        )

        if agent.preferred_contact_endpoint_id != user_sms_endpoint.id:
            agent.preferred_contact_endpoint = user_sms_endpoint
            agent.save(update_fields=["preferred_contact_endpoint"])

        conversation = _get_or_create_conversation(
            channel=CommsChannel.SMS.value,
            address=phone.phone_number,
            owner_agent=agent,
        )
        _ensure_participant(
            conversation,
            user_sms_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
        )
        _ensure_participant(
            conversation,
            agent_sms_endpoint,
            PersistentAgentConversationParticipant.ParticipantRole.AGENT,
        )

        if created:
            try:
                sms.send_sms(
                    to_number=phone.phone_number,
                    from_number=agent_sms_endpoint.address,
                    body="Gobii: You've enabled SMS communication with Gobii. Reply HELP for help, STOP to opt-out.",
                )
            except Exception:
                pass

            PersistentAgentMessage.objects.create(
                is_outbound=False,
                from_endpoint=user_sms_endpoint,
                to_endpoint=agent_sms_endpoint,
                conversation=conversation,
                body=(
                    "Hi I've enabled SMS communication with you! "
                    "Could you introduce yourself and confirm SMS is working?"
                ),
                owner_agent=agent,
            )

        transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.id)))

    return agent_sms_endpoint, user_sms_endpoint
