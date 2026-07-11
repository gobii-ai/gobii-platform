import json
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.text import slugify
from waffle import flag_is_active

from api.models import Organization, OrganizationMembership
from api.services.organization_permissions import user_role_can_create_org_agents
from console.forms import OrganizationForm
from console.agent_context import resolve_context_override_for_agent
from console.context_helpers import build_console_context, resolve_console_context
from console.context_overrides import get_context_override
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource


def _unique_organization_slug(name: str) -> str:
    base_slug = slugify(name) or "organization"
    slug = base_slug
    suffix = 2
    while Organization.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _serialize_context(
    context_type: str,
    context_id: str,
    context_name: str,
    *,
    can_create_agents: bool = True,
) -> dict:
    return {
        "type": context_type,
        "id": str(context_id),
        "name": context_name,
        "canCreateAgents": can_create_agents,
    }


class SwitchContextView(LoginRequiredMixin, View):
    """Handle switching between personal and organization contexts."""

    def get(self, request):
        override = get_context_override(request)
        for_agent_id = (request.GET.get("for_agent") or "").strip()
        requested_agent_status = None
        if for_agent_id:
            override, error_code = resolve_context_override_for_agent(
                request.user,
                for_agent_id,
                include_deleted=True,
            )
            if error_code == "not_found":
                requested_agent_status = "missing"
            if error_code == "forbidden":
                return JsonResponse({"error": "Not permitted"}, status=403)
            if error_code == "deleted":
                requested_agent_status = "deleted"
        if override:
            try:
                resolved = resolve_console_context(request.user, request.session, override=override)
            except PermissionDenied:
                return JsonResponse({"error": "Invalid context override."}, status=403)
        else:
            resolved = build_console_context(request)
        current_context = resolved.current_context

        if not override:
            session_context = {
                "type": request.session.get("context_type"),
                "id": request.session.get("context_id"),
                "name": request.session.get("context_name"),
            }
            if (
                session_context["type"] != current_context.type
                or session_context["id"] != current_context.id
                or session_context["name"] != current_context.name
            ):
                request.session["context_type"] = current_context.type
                request.session["context_id"] = current_context.id
                request.session["context_name"] = current_context.name

        organizations_enabled = flag_is_active(request, "organizations")
        organizations = []
        if organizations_enabled:
            memberships = (
                OrganizationMembership.objects.filter(
                    user=request.user,
                    status=OrganizationMembership.OrgStatus.ACTIVE,
                )
                .select_related("org")
                .order_by("org__name")
            )
            organizations = [
                {
                    "id": str(membership.org.id),
                    "name": membership.org.name,
                    "role": membership.get_role_display(),
                    "canCreateAgents": user_role_can_create_org_agents(membership.role, membership.org),
                }
                for membership in memberships
            ]

        personal_name = request.user.get_full_name() or request.user.username or request.user.email or "Personal"
        return JsonResponse(
            {
                "context": {
                    "type": current_context.type,
                    "id": current_context.id,
                    "name": current_context.name,
                    "canCreateAgents": resolved.can_create_org_agents,
                },
                "personal": {"id": str(request.user.id), "name": personal_name},
                "organizations": organizations,
                "organizations_enabled": organizations_enabled,
                "requested_agent_status": requested_agent_status,
            }
        )

    def post(self, request):
        """Save the selected context to session."""
        try:
            data = json.loads(request.body)
            context_type = data.get('type')
            context_id = data.get('id')
            persist_raw = data.get('persist', True)
            if isinstance(persist_raw, str):
                persist = persist_raw.strip().lower() not in ['0', 'false', 'no', 'off']
            else:
                persist = bool(persist_raw)
            
            # Validate context type
            if context_type not in ['personal', 'organization']:
                return JsonResponse({'error': 'Invalid context type'}, status=400)
            
            # If personal context, validate it's the current user
            if context_type == 'personal':
                can_create_agents = True
                if str(request.user.id) != context_id:
                    return JsonResponse({'error': 'Invalid personal context'}, status=403)
                context_name = request.user.get_full_name() or request.user.username or request.user.email or "Personal"
                if persist:
                    # Store in session
                    request.session['context_type'] = 'personal'
                    request.session['context_id'] = str(request.user.id)
                    request.session['context_name'] = context_name
                
            # If organization context, validate membership
            elif context_type == 'organization':
                try:
                    membership = OrganizationMembership.objects.get(
                        user=request.user,
                        org_id=context_id,
                        status=OrganizationMembership.OrgStatus.ACTIVE
                    )
                    context_name = membership.org.name
                    can_create_agents = user_role_can_create_org_agents(membership.role, membership.org)
                    if persist:
                        # Store in session
                        request.session['context_type'] = 'organization'
                        request.session['context_id'] = str(membership.org.id)
                        request.session['context_name'] = context_name
                    
                except OrganizationMembership.DoesNotExist:
                    return JsonResponse({'error': 'Invalid organization context'}, status=403)
            
            return JsonResponse({
                'success': True,
                'context': _serialize_context(
                    context_type,
                    str(context_id),
                    context_name,
                    can_create_agents=can_create_agents,
                ),
            })
            
        except Exception as e:
            # Consider logging the exception here for debugging, e.g.:
            # import logging
            # logging.exception("Error switching context")
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)


class OrganizationCreateAPIView(LoginRequiredMixin, View):
    """Create an organization from the live chat context picker."""

    http_method_names = ["post"]

    @transaction.atomic
    def post(self, request):
        if not flag_is_active(request, "organizations"):
            return JsonResponse({"error": "Organizations are not available."}, status=404)

        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"errors": {"__all__": ["Invalid JSON body."]}}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({"errors": {"__all__": ["JSON object expected."]}}, status=400)

        form = OrganizationForm(data={"name": payload.get("name", "")})
        if not form.is_valid():
            return JsonResponse(
                {
                    "errors": {
                        field: [str(message) for message in messages]
                        for field, messages in form.errors.items()
                    },
                },
                status=400,
            )

        org = form.save(commit=False)
        org.slug = _unique_organization_slug(org.name)
        org.created_by = request.user
        try:
            org.save()
        except IntegrityError:
            return JsonResponse(
                {"errors": {"name": ["Unable to create an organization with that name."]}},
                status=400,
            )

        owner_membership = OrganizationMembership.objects.create(
            org=org,
            user=request.user,
            role=OrganizationMembership.OrgRole.OWNER,
        )

        request.session["context_type"] = "organization"
        request.session["context_id"] = str(org.id)
        request.session["context_name"] = org.name

        created_props = Analytics.with_org_properties(
            {"organization_slug": org.slug},
            organization=org,
        )
        member_props = Analytics.with_org_properties(
            {
                "member_id": str(request.user.id),
                "member_role": owner_membership.role,
                "actor_id": str(request.user.id),
            },
            organization=org,
        )
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_CREATED,
            source=AnalyticsSource.WEB,
            properties=created_props.copy(),
        ))
        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.ORGANIZATION_MEMBER_ADDED,
            source=AnalyticsSource.WEB,
            properties=member_props.copy(),
        ))

        return JsonResponse(
            {
                "organization": {
                    "id": str(org.id),
                    "name": org.name,
                    "role": owner_membership.get_role_display(),
                },
                "context": _serialize_context("organization", str(org.id), org.name),
            },
            status=201,
        )
