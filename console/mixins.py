import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404

from api.models import OrganizationMembership

from .context_helpers import build_console_context
from util.integrations import stripe_status
from util.subscription_helper import get_user_plan
from constants.plans import PlanNames

logger = logging.getLogger(__name__)


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

            # Add user's subscription plan for frontend
            # Normalize plan IDs to frontend-friendly values: free, startup, scale
            try:
                plan = get_user_plan(self.request.user)
                plan_id = str(plan.get("id", "")).lower() if plan else ""
                # Map internal plan IDs to frontend values
                plan_map = {
                    PlanNames.FREE: 'free',
                    PlanNames.STARTUP: 'startup',
                    PlanNames.SCALE: 'scale',
                }
                context['user_plan'] = plan_map.get(plan_id, "")
            except Exception:
                logger.exception("Error fetching user plan for context")
                context['user_plan'] = ""

        context['stripe_enabled'] = stripe_status().enabled

        return context


class ConsoleViewMixin(LoginRequiredMixin, ConsoleContextMixin):
    """Base mixin for all console views."""
    pass


class SystemAdminRequiredMixin(ConsoleViewMixin):
    """Restrict access to console surfaces that only staff/system admins should see."""

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied("You do not have permission to access this console area.")
        return super().dispatch(request, *args, **kwargs)


class StripeFeatureRequiredMixin:
    """Mixin to gate views when Stripe billing is disabled."""

    def dispatch(self, request, *args, **kwargs):
        status = stripe_status()
        if not status.enabled:
            raise Http404("Billing features are not available in this deployment.")
        return super().dispatch(request, *args, **kwargs)
