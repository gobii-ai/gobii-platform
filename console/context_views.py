import json
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from waffle import flag_is_active

from api.models import OrganizationMembership
from console.context_helpers import build_console_context, resolve_console_context
from console.context_overrides import get_context_override


class SwitchContextView(LoginRequiredMixin, View):
    """Handle switching between personal and organization contexts."""

    def get(self, request):
        override = get_context_override(request)
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
                },
                "personal": {"id": str(request.user.id), "name": personal_name},
                "organizations": organizations,
                "organizations_enabled": organizations_enabled,
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
                    if persist:
                        # Store in session
                        request.session['context_type'] = 'organization'
                        request.session['context_id'] = str(membership.org.id)
                        request.session['context_name'] = context_name
                    
                except OrganizationMembership.DoesNotExist:
                    return JsonResponse({'error': 'Invalid organization context'}, status=403)
            
            return JsonResponse({
                'success': True,
                'context': {
                    'type': context_type,
                    'id': str(context_id),
                    'name': context_name
                }
            })
            
        except Exception as e:
            # Consider logging the exception here for debugging, e.g.:
            # import logging
            # logging.exception("Error switching context")
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)
