import logging
import smtplib
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from anymail.exceptions import AnymailError
from waffle import flag_is_active

from agents.services import PretrainedWorkerTemplateService
from api.models import (
    IntelligenceTier,
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    PersistentAgent,
    PersistentAgentTemplate,
)
from api.agent.core.llm_config import AgentLLMTier
from api.services.agent_owner_custom_instructions import (
    CUSTOM_INSTRUCTIONS_FIELD,
    CustomInstructionsValidationError,
    get_custom_instructions_for_organization_id,
    normalize_custom_instructions,
    save_custom_instructions_for_organization_id,
)
from api.services.organization_permissions import (
    ORG_AGENT_CONFIG_AUTHORITY_ROLES,
    ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS,
    organization_members_can_create_agents,
    user_role_can_create_org_agents,
)
from api.services.template_clone import TemplateCloneError, TemplateCloneService
from console.api_helpers import _parse_json_body as _parse_json_body_or_raise
from console.context_helpers import build_console_context
from console.forms import OrganizationForm, OrganizationInviteForm
from console.views import build_llm_intelligence_props
from console.agent_creation import (
    AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY,
    AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE,
    AGENT_TEMPLATE_SOURCE_SESSION_KEY,
)
from console.role_constants import BILLING_MANAGE_ROLES, MEMBER_MANAGE_ROLES
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from util.urls import IMMERSIVE_APP_BASE_PATH

logger = logging.getLogger(__name__)

OWNER_EQUIVALENT_ROLES = (
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.SOLUTIONS_PARTNER,
)
ORG_CUSTOM_INSTRUCTIONS_FIELD = CUSTOM_INSTRUCTIONS_FIELD
ORG_MEMBERS_CAN_CREATE_AGENTS_FIELD = "membersCanCreateAgents"
PREFERRED_LLM_TIER_SESSION_KEY = "agent_preferred_llm_tier"
TEMPLATE_NAME_MAX_LENGTH = 255
TEMPLATE_TAGLINE_MAX_LENGTH = 255


def _json_error(message: str, *, status: int = 400):
    return JsonResponse({"error": message}, status=status)


def _json_field_errors(errors, *, status: int = 400):
    return JsonResponse(
        {
            "errors": {
                field: [str(message) for message in messages]
                for field, messages in errors.items()
            },
        },
        status=status,
    )


def _first_payload_value(payload: dict, *keys: str):
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _parse_json_body(request):
    try:
        payload = _parse_json_body_or_raise(request)
    except ValueError as exc:
        return None, _json_field_errors({"__all__": [str(exc)]})
    return payload, None


def _resolve_allowed_role_choices_for_role(role: str | None) -> list[tuple[str, str]]:
    all_role_choices = list(OrganizationMembership.OrgRole.choices)
    if role in OWNER_EQUIVALENT_ROLES:
        return all_role_choices
    if role == OrganizationMembership.OrgRole.ADMIN:
        return [
            choice
            for choice in all_role_choices
            if choice[0] not in OWNER_EQUIVALENT_ROLES
        ]
    return []


def _active_invites(org: Organization):
    return OrganizationInvite.objects.filter(
        org=org,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gte=timezone.now(),
    ).select_related("invited_by").order_by("email")


def _serialize_member(membership: OrganizationMembership, viewer_membership: OrganizationMembership) -> dict:
    user = membership.user
    display_name = user.get_full_name() or user.username or user.email or "Member"
    allowed_role_values = {
        value
        for value, _label in _resolve_allowed_role_choices_for_role(viewer_membership.role)
    }
    can_manage = viewer_membership.role in MEMBER_MANAGE_ROLES
    target_owner_equivalent = membership.role in OWNER_EQUIVALENT_ROLES
    viewer_is_admin = viewer_membership.role == OrganizationMembership.OrgRole.ADMIN
    return {
        "userId": str(user.id),
        "name": display_name,
        "email": user.email or "",
        "role": membership.role,
        "roleLabel": membership.get_role_display(),
        "isCurrentUser": user.id == viewer_membership.user_id,
        "canUpdateRole": can_manage and membership.role in allowed_role_values,
        "canRemove": (
            can_manage
            and user.id != viewer_membership.user_id
            and not (viewer_is_admin and target_owner_equivalent)
        ),
    }


def _serialize_invite(invite: OrganizationInvite) -> dict:
    return {
        "token": invite.token,
        "email": invite.email,
        "role": invite.role,
        "roleLabel": invite.get_role_display(),
        "invitedBy": invite.invited_by.email or invite.invited_by.username,
        "sentAt": invite.sent_at.isoformat() if invite.sent_at else None,
        "expiresAt": invite.expires_at.isoformat() if invite.expires_at else None,
    }


def _serialize_organization(org: Organization, membership: OrganizationMembership) -> dict:
    role_choices = _resolve_allowed_role_choices_for_role(membership.role)
    billing = getattr(org, "billing", None)
    custom_instructions = get_custom_instructions_for_organization_id(org.id)
    return {
        "organization": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "plan": org.plan,
            "customInstructions": custom_instructions,
            "customInstructionsMaxChars": settings.AGENT_OWNER_CUSTOM_INSTRUCTIONS_MAX_CHARS,
            "membersCanCreateAgents": organization_members_can_create_agents(org),
        },
        "viewer": {
            "role": membership.role,
            "roleLabel": membership.get_role_display(),
            "canEditOrganization": membership.role in OWNER_EQUIVALENT_ROLES,
            "canEditCustomInstructions": membership.role in ORG_AGENT_CONFIG_AUTHORITY_ROLES,
            "canEditMemberAgentCreation": membership.role in ORG_AGENT_CONFIG_AUTHORITY_ROLES,
            "canManageMembers": membership.role in MEMBER_MANAGE_ROLES,
            "canManageBilling": membership.role in BILLING_MANAGE_ROLES,
        },
        "roles": [
            {"value": value, "label": label}
            for value, label in role_choices
        ],
        "members": [
            _serialize_member(member, membership)
            for member in OrganizationMembership.objects.filter(
                org=org,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).select_related("user").order_by("user__email")
        ],
        "pendingInvites": [
            _serialize_invite(invite)
            for invite in _active_invites(org)
        ],
        "billing": {
            "purchasedSeats": getattr(billing, "purchased_seats", None),
            "seatsReserved": getattr(billing, "seats_reserved", None),
            "seatsAvailable": getattr(billing, "seats_available", None),
        } if billing else None,
    }


def _serialize_organization_template(template: PersistentAgentTemplate) -> dict:
    preferred_llm_tier = getattr(template, "preferred_llm_tier", None)
    preferred_llm_tier_key = getattr(preferred_llm_tier, "key", None) or "standard"
    return {
        "id": str(template.id),
        "name": template.display_name,
        "tagline": template.tagline,
        "charter": template.charter,
        "category": template.category or "Custom",
        "preferredLlmTier": preferred_llm_tier_key,
    }


def _organization_template_queryset(org: Organization):
    return (
        PersistentAgentTemplate.objects
        .select_related("preferred_llm_tier")
        .filter(
            organization=org,
            public_profile__isnull=True,
            is_active=True,
        )
        .order_by("priority", "display_name")
    )


def _serialize_source_agent(agent: PersistentAgent) -> dict:
    return {
        "id": str(agent.id),
        "name": agent.name or "Untitled Agent",
    }


def _template_preferred_llm_tier_key(template: PersistentAgentTemplate) -> str | None:
    preferred_llm_tier = getattr(template, "preferred_llm_tier", None)
    tier_key = getattr(preferred_llm_tier, "key", preferred_llm_tier)
    tier_key = str(tier_key or "").strip().lower()
    return tier_key or None


def _serialize_organization_templates(org: Organization, membership: OrganizationMembership) -> dict:
    can_manage_templates = membership.role in MEMBER_MANAGE_ROLES
    source_agents = []
    if can_manage_templates:
        source_agents = [
            _serialize_source_agent(agent)
            for agent in (
                PersistentAgent.objects
                .non_eval()
                .alive()
                .filter(organization=org)
                .order_by("name", "id")
            )
        ]
    return {
        "organization": {
            "id": str(org.id),
            "name": org.name,
        },
        "viewer": {
            "canManageTemplates": can_manage_templates,
        },
        "templates": [
            _serialize_organization_template(template)
            for template in _organization_template_queryset(org)
        ],
        "sourceAgents": source_agents,
        "llmIntelligence": build_llm_intelligence_props(
            org,
            "organization",
            org,
            None,
        ),
    }


def _resolve_current_org(request):
    if not flag_is_active(request, "organizations"):
        raise PermissionDenied("Organizations are not available.")
    resolved = build_console_context(request)
    if resolved.current_context.type != "organization" or not resolved.current_membership:
        return None, None
    return resolved.current_membership.org, resolved.current_membership


def _require_current_org(request):
    org, membership = _resolve_current_org(request)
    if not org or not membership:
        return None, None, _json_error("Switch to an organization context first.", status=404)
    return org, membership, None


def _require_current_org_role(request, roles, message: str):
    try:
        org, membership, error = _require_current_org(request)
    except PermissionDenied as exc:
        return None, None, _json_error(str(exc), status=404)
    if error:
        return None, None, error
    if membership.role not in roles:
        return None, None, _json_error(message, status=403)
    return org, membership, None


def _lock_organization(org: Organization) -> Organization:
    return Organization.objects.select_for_update().get(pk=org.pk)


def _normalize_custom_instructions_payload(payload: dict) -> tuple[str | None, JsonResponse | None]:
    try:
        return normalize_custom_instructions(payload.get(ORG_CUSTOM_INSTRUCTIONS_FIELD)), None
    except CustomInstructionsValidationError as exc:
        return None, _json_field_errors({ORG_CUSTOM_INSTRUCTIONS_FIELD: [str(exc)]})


def _normalize_members_can_create_agents_payload(payload: dict) -> tuple[bool | None, JsonResponse | None]:
    if ORG_MEMBERS_CAN_CREATE_AGENTS_FIELD not in payload:
        return None, None
    value = payload.get(ORG_MEMBERS_CAN_CREATE_AGENTS_FIELD)
    if isinstance(value, bool):
        return value, None
    return None, _json_field_errors({ORG_MEMBERS_CAN_CREATE_AGENTS_FIELD: ["Use true or false."]})


def _save_members_can_create_agents_setting(org: Organization, enabled: bool) -> None:
    org_settings = org.org_settings if isinstance(org.org_settings, dict) else {}
    next_settings = dict(org_settings)
    if enabled:
        next_settings[ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS] = True
    else:
        next_settings.pop(ORG_SETTING_MEMBERS_CAN_CREATE_AGENTS, None)
    org.org_settings = next_settings
    org.save(update_fields=["org_settings", "updated_at"])


def _normalize_template_editor_payload(payload: dict, org: Organization):
    name = str(_first_payload_value(payload, "name", "displayName", "display_name") or "").strip()
    tagline = str(_first_payload_value(payload, "tagline", "description") or "").strip()
    charter = str(_first_payload_value(payload, "charter", "instructions") or "").strip()
    preferred_llm_tier_key = str(
        _first_payload_value(payload, "preferredLlmTier", "preferred_llm_tier") or ""
    ).strip().lower()

    errors: dict[str, list[str]] = {}
    if not name:
        errors["name"] = ["Name is required."]
    elif len(name) > TEMPLATE_NAME_MAX_LENGTH:
        errors["name"] = [f"Name must be {TEMPLATE_NAME_MAX_LENGTH} characters or fewer."]

    if not tagline:
        errors["tagline"] = ["Short description is required."]
    elif len(tagline) > TEMPLATE_TAGLINE_MAX_LENGTH:
        errors["tagline"] = [f"Short description must be {TEMPLATE_TAGLINE_MAX_LENGTH} characters or fewer."]

    if not charter:
        errors["charter"] = ["Instructions are required."]

    if not preferred_llm_tier_key:
        default_tier = build_llm_intelligence_props(org, "organization", org, None).get("systemDefaultTier")
        preferred_llm_tier_key = str(default_tier or AgentLLMTier.STANDARD.value).strip().lower()

    try:
        AgentLLMTier(preferred_llm_tier_key)
    except ValueError:
        errors["preferredLlmTier"] = ["Choose a valid intelligence level."]

    if errors:
        return None, _json_field_errors(errors)

    preferred_llm_tier = IntelligenceTier.objects.filter(key=preferred_llm_tier_key).first()
    if preferred_llm_tier is None:
        return None, _json_field_errors({"preferredLlmTier": ["Choose a valid intelligence level."]})

    return {
        "display_name": name,
        "tagline": tagline,
        "description": tagline,
        "charter": charter,
        "preferred_llm_tier": preferred_llm_tier,
    }, None


def _send_invitation_email(request, org: Organization, invite: OrganizationInvite) -> None:
    accept_url = request.build_absolute_uri(
        f"/app/organizations/invites/{invite.token}/accept"
    )
    reject_url = request.build_absolute_uri(
        reverse("org_invite_reject", kwargs={"token": invite.token})
    )
    context = {
        "org": org,
        "invited_by": request.user,
        "invite": invite,
        "accept_url": accept_url,
        "reject_url": reject_url,
    }
    html_body = render_to_string("emails/organization_invite.html", context)
    text_body = render_to_string("emails/organization_invite.txt", context)
    subject = f"You're invited to join {org.name} on Gobii"
    send_mail(
        subject=subject,
        message=text_body,
        from_email=None,
        recipient_list=[invite.email],
        html_message=html_body,
        fail_silently=False,
    )


class CurrentOrganizationAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "patch"]

    def get(self, request):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        return JsonResponse(_serialize_organization(org, membership))

    @transaction.atomic
    def patch(self, request):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error

        payload, error = _parse_json_body(request)
        if error:
            return error

        name_supplied = "name" in payload
        custom_instructions_supplied = ORG_CUSTOM_INSTRUCTIONS_FIELD in payload
        members_can_create_agents_supplied = ORG_MEMBERS_CAN_CREATE_AGENTS_FIELD in payload
        if not name_supplied and not custom_instructions_supplied and not members_can_create_agents_supplied:
            return _json_error("No organization updates were provided.", status=400)

        if name_supplied and membership.role not in OWNER_EQUIVALENT_ROLES:
            return _json_error("You do not have permission to edit this organization.", status=403)
        if custom_instructions_supplied and membership.role not in ORG_AGENT_CONFIG_AUTHORITY_ROLES:
            return _json_error("You do not have permission to edit custom instructions.", status=403)
        if members_can_create_agents_supplied and membership.role not in ORG_AGENT_CONFIG_AUTHORITY_ROLES:
            return _json_error("You do not have permission to edit agent creation settings.", status=403)

        normalized_instructions = None
        if custom_instructions_supplied:
            normalized_instructions, error = _normalize_custom_instructions_payload(payload)
            if error:
                return error
        members_can_create_agents = None
        if members_can_create_agents_supplied:
            members_can_create_agents, error = _normalize_members_can_create_agents_payload(payload)
            if error:
                return error

        org = _lock_organization(org)
        previous_name = org.name
        if name_supplied:
            form = OrganizationForm(data={"name": payload.get("name", "")}, instance=org)
            if not form.is_valid():
                return _json_field_errors(form.errors)
            org = form.save()
            request.session["context_type"] = "organization"
            request.session["context_id"] = str(org.id)
            request.session["context_name"] = org.name

        if custom_instructions_supplied and normalized_instructions is not None:
            save_custom_instructions_for_organization_id(
                org.id,
                instructions=normalized_instructions,
                updated_by=request.user,
            )
        if members_can_create_agents is not None:
            _save_members_can_create_agents_setting(org, members_can_create_agents)

        if name_supplied and previous_name != org.name:
            props = Analytics.with_org_properties(
                {
                    "actor_id": str(request.user.id),
                    "old_name": previous_name,
                    "new_name": org.name,
                },
                organization=org,
            )
            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.ORGANIZATION_UPDATED,
                source=AnalyticsSource.WEB,
                properties=props.copy(),
            ))
        return JsonResponse(_serialize_organization(org, membership))


class CurrentOrganizationTemplateAPIView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        return JsonResponse(_serialize_organization_templates(org, membership))

    @transaction.atomic
    def post(self, request):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage templates.")
        if error:
            return error

        payload, error = _parse_json_body(request)
        if error:
            return error

        source_agent_id = str(payload.get("sourceAgentId") or payload.get("source_agent_id") or "").strip()
        if source_agent_id:
            try:
                source_agent_uuid = uuid.UUID(source_agent_id)
            except (TypeError, ValueError, AttributeError):
                return _json_error("sourceAgentId must be a valid UUID.", status=400)

            source_agent = get_object_or_404(
                PersistentAgent.objects.non_eval().alive().select_related("organization"),
                id=source_agent_uuid,
                organization=org,
            )
            try:
                result = TemplateCloneService.clone_agent_to_organization_template(
                    agent=source_agent,
                    user=request.user,
                )
            except TemplateCloneError as exc:
                return _json_error(str(exc), status=400)
            except ValidationError as exc:
                message = exc.messages[0] if getattr(exc, "messages", None) else "Unable to create template."
                return _json_error(message, status=400)

            return JsonResponse(
                {
                    **_serialize_organization_templates(org, membership),
                    "created": result.created,
                    "templateId": str(result.template.id),
                },
                status=201 if result.created else 200,
            )

        normalized, error = _normalize_template_editor_payload(payload, org)
        if error:
            return error

        template = PersistentAgentTemplate(
            code=TemplateCloneService._generate_template_code(),
            organization=org,
            created_by=request.user,
            category="Custom",
            recommended_contact_channel="email",
            **normalized,
        )
        try:
            template.full_clean()
        except ValidationError as exc:
            return _json_field_errors(exc.message_dict if hasattr(exc, "message_dict") else {"__all__": exc.messages})
        template.save()
        return JsonResponse(
            {
                **_serialize_organization_templates(org, membership),
                "created": True,
                "templateId": str(template.id),
            },
            status=201,
        )


class CurrentOrganizationTemplateDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    @transaction.atomic
    def patch(self, request, template_id):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage templates.")
        if error:
            return error

        payload, error = _parse_json_body(request)
        if error:
            return error
        normalized, error = _normalize_template_editor_payload(payload, org)
        if error:
            return error

        template = get_object_or_404(
            PersistentAgentTemplate.objects.select_related("preferred_llm_tier"),
            id=template_id,
            organization=org,
            public_profile__isnull=True,
            is_active=True,
        )
        for field, value in normalized.items():
            setattr(template, field, value)
        try:
            template.full_clean()
        except ValidationError as exc:
            return _json_field_errors(exc.message_dict if hasattr(exc, "message_dict") else {"__all__": exc.messages})
        template.save(update_fields=[
            "display_name",
            "tagline",
            "description",
            "charter",
            "preferred_llm_tier",
            "updated_at",
        ])
        return JsonResponse({
            **_serialize_organization_templates(org, membership),
            "template": _serialize_organization_template(template),
            "templateId": str(template.id),
        })

    @transaction.atomic
    def delete(self, request, template_id):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage templates.")
        if error:
            return error

        template = get_object_or_404(
            PersistentAgentTemplate.objects.select_related("preferred_llm_tier"),
            id=template_id,
            organization=org,
            public_profile__isnull=True,
            is_active=True,
        )
        template.is_active = False
        template.save(update_fields=["is_active", "updated_at"])
        return JsonResponse(_serialize_organization_templates(org, membership))


class CurrentOrganizationTemplateLaunchAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, template_id):
        try:
            org, membership, error = _require_current_org(request)
        except PermissionDenied as exc:
            return _json_error(str(exc), status=404)
        if error:
            return error
        if not user_role_can_create_org_agents(membership.role, org):
            return _json_error("You do not have permission to create agents for this organization.", status=403)

        template = get_object_or_404(
            PersistentAgentTemplate.objects.select_related("preferred_llm_tier"),
            id=template_id,
            organization=org,
            public_profile__isnull=True,
            is_active=True,
        )
        request.session["context_type"] = "organization"
        request.session["context_id"] = str(org.id)
        request.session["context_name"] = org.name
        request.session["agent_charter"] = template.charter
        request.session["agent_charter_source"] = "template"
        request.session[PretrainedWorkerTemplateService.TEMPLATE_SESSION_KEY] = template.code
        request.session[AGENT_TEMPLATE_SOURCE_SESSION_KEY] = AGENT_TEMPLATE_SOURCE_ORGANIZATION_TEMPLATE
        request.session[AGENT_TEMPLATE_ORGANIZATION_SESSION_KEY] = str(org.id)
        tier_key = _template_preferred_llm_tier_key(template)
        if tier_key:
            request.session[PREFERRED_LLM_TIER_SESSION_KEY] = tier_key
        else:
            request.session.pop(PREFERRED_LLM_TIER_SESSION_KEY, None)
        request.session.modified = True
        return JsonResponse({
            "templateId": str(template.id),
            "redirectUrl": f"{IMMERSIVE_APP_BASE_PATH}/agents/new?spawn=1",
        })


class CurrentOrganizationInviteAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage members.")
        if error:
            return error

        payload, error = _parse_json_body(request)
        if error:
            return error
        allowed_roles = _resolve_allowed_role_choices_for_role(membership.role)
        form = OrganizationInviteForm(
            data={
                "email": payload.get("email", ""),
                "role": payload.get("role", ""),
            },
            org=org,
            allowed_roles=allowed_roles,
        )
        billing = getattr(org, "billing", None)
        invite_role = str(payload.get("role") or "")
        if (
            billing
            and billing.seats_available <= 0
            and invite_role != OrganizationMembership.OrgRole.SOLUTIONS_PARTNER
        ):
            form.add_error(None, "No seats available. Increase the seat count before inviting new members.")
        if not form.is_valid():
            return _json_field_errors(form.errors)

        invite = OrganizationInvite.objects.create(
            org=org,
            email=form.cleaned_data["email"],
            role=form.cleaned_data["role"],
            token=uuid.uuid4().hex,
            expires_at=timezone.now() + timedelta(days=7),
            invited_by=request.user,
        )
        props = Analytics.with_org_properties(
            {
                "invite_id": str(invite.id),
                "invite_token": invite.token,
                "invite_role": invite.role,
                "invite_email": invite.email,
                "actor_id": str(request.user.id),
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_SENT,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        try:
            _send_invitation_email(request, org, invite)
        except (AnymailError, OSError, smtplib.SMTPException) as exc:
            logger.warning("Failed sending org invite email: %s", exc)

        return JsonResponse(_serialize_organization(org, membership), status=201)


class CurrentOrganizationInviteDetailAPIView(LoginRequiredMixin, View):
    http_method_names = ["delete"]

    @transaction.atomic
    def delete(self, request, token: str):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage members.")
        if error:
            return error

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at:
            return _json_error("Invite is already finalized.", status=400)

        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at"])
        props = Analytics.with_org_properties(
            {
                "invite_id": str(invite.id),
                "invite_token": invite.token,
                "actor_id": str(request.user.id),
                "reason": "revoked",
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_DECLINED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))


class CurrentOrganizationInviteResendAPIView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request, token: str):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage members.")
        if error:
            return error

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at or invite.expires_at < timezone.now():
            return _json_error("Invite is no longer valid.", status=400)

        invite.sent_at = timezone.now()
        invite.save(update_fields=["sent_at"])
        props = Analytics.with_org_properties(
            {
                "invite_id": str(invite.id),
                "invite_token": invite.token,
                "actor_id": str(request.user.id),
                "resend": True,
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_INVITE_SENT,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        try:
            _send_invitation_email(request, org, invite)
        except (AnymailError, OSError, smtplib.SMTPException) as exc:
            logger.warning("Failed resending org invite email: %s", exc)

        return JsonResponse(_serialize_organization(org, membership))


class CurrentOrganizationMemberAPIView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    @transaction.atomic
    def patch(self, request, user_id: int):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage members.")
        if error:
            return error

        payload, error = _parse_json_body(request)
        if error:
            return error
        new_role = str(payload.get("role") or "")
        allowed_role_values = {value for value, _label in _resolve_allowed_role_choices_for_role(membership.role)}
        if new_role not in allowed_role_values:
            return _json_error("Invalid role.", status=403)

        org = _lock_organization(org)
        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        if target_membership.role == new_role:
            return JsonResponse(_serialize_organization(org, membership))

        if (
            membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role in OWNER_EQUIVALENT_ROLES
        ):
            return _json_error("Admins cannot modify owner-equivalent roles.", status=403)

        if (
            target_membership.role == OrganizationMembership.OrgRole.OWNER
            and new_role != OrganizationMembership.OrgRole.OWNER
            and OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count() <= 1
        ):
            return _json_error("You must keep at least one owner in the organization.", status=400)

        previous_role = target_membership.role
        target_membership.role = new_role
        target_membership.save(update_fields=["role"])
        props = Analytics.with_org_properties(
            {
                "member_id": str(target_membership.user_id),
                "actor_id": str(request.user.id),
                "old_role": previous_role,
                "new_role": new_role,
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_ROLE_UPDATED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))

    @transaction.atomic
    def delete(self, request, user_id: int):
        org, membership, error = _require_current_org_role(request, MEMBER_MANAGE_ROLES, "You do not have permission to manage members.")
        if error:
            return error
        if request.user.id == user_id:
            return _json_error("You cannot remove yourself.", status=400)

        org = _lock_organization(org)
        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )
        if (
            membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role in OWNER_EQUIVALENT_ROLES
        ):
            return _json_error("Admins cannot remove owner-equivalent roles.", status=403)

        if (
            target_membership.role == OrganizationMembership.OrgRole.OWNER
            and OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count() <= 1
        ):
            return _json_error("You must keep at least one owner in the organization.", status=400)

        target_membership.status = OrganizationMembership.OrgStatus.REMOVED
        target_membership.save(update_fields=["status"])
        props = Analytics.with_org_properties(
            {
                "member_id": str(target_membership.user_id),
                "member_role": target_membership.role,
                "actor_id": str(request.user.id),
                "reason": "removed_by_admin",
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_REMOVED,
            source=AnalyticsSource.WEB,
            properties=props.copy(),
        ))
        return JsonResponse(_serialize_organization(org, membership))
