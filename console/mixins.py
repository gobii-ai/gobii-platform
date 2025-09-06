from django.contrib.auth.mixins import LoginRequiredMixin
from api.models import OrganizationMembership


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
            
            # Get current context from session or default to personal
            context_type = self.request.session.get('context_type', 'personal')
            context_id = self.request.session.get('context_id', str(self.request.user.id))
            context_name = self.request.session.get('context_name', self.request.user.get_full_name() or self.request.user.username)
            
            context['current_context'] = {
                'type': context_type,
                'id': context_id,
                'name': context_name
            }
            
            # If in organization context, add the membership for role checking
            if context_type == 'organization':
                try:
                    context['current_membership'] = OrganizationMembership.objects.get(
                        user=self.request.user,
                        org_id=context_id,
                        status=OrganizationMembership.OrgStatus.ACTIVE
                    )
                except OrganizationMembership.DoesNotExist:
                    # Reset to personal context if membership doesn't exist
                    context['current_context'] = {
                        'type': 'personal',
                        'id': str(self.request.user.id),
                        'name': self.request.user.get_full_name() or self.request.user.username
                    }
        
        return context


class ConsoleViewMixin(LoginRequiredMixin, ConsoleContextMixin):
    """Base mixin for all console views."""
    pass