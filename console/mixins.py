from django.contrib.auth.mixins import LoginRequiredMixin

from api.models import OrganizationMembership

from .context_helpers import build_console_context


class ConsoleContextMixin:
    """Mixin to add console-specific context data including organization memberships."""
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get user's organization memberships with active status
        if self.request.user.is_authenticated:
            context['user_organizations'] = OrganizationMembership.objects.filter(
                user=self.request.user,
                status=OrganizationMembership.OrgStatus.ACTIVE
            ).select_related('org').order_by('org__name')

            resolved = build_console_context(self.request)
            context['current_context'] = {
                'type': resolved.current_context.type,
                'id': resolved.current_context.id,
                'name': resolved.current_context.name,
            }

            if resolved.current_membership is not None:
                context['current_membership'] = resolved.current_membership

            context['can_manage_org_agents'] = resolved.can_manage_org_agents
        
        return context


class ConsoleViewMixin(LoginRequiredMixin, ConsoleContextMixin):
    """Base mixin for all console views."""
    pass
