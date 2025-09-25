import json
from decimal import Decimal

import stripe
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.utils.html import strip_tags
from django.utils.html import format_html
from django.views.generic import TemplateView, ListView, View, DetailView
from django.views.generic.edit import FormMixin
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.db import transaction, models
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, HttpResponse, JsonResponse, Http404
from django.core.exceptions import ValidationError, PermissionDenied
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta, datetime, timezone as dt_timezone
import uuid

from agents.services import AgentService, AIEmployeeTemplateService
from api.models import ApiKey, UserBilling, PersistentAgent, BrowserUseAgent, PersistentAgentCommsEndpoint, \
    PersistentAgentEmailEndpoint, CommsChannel, PersistentAgentConversation, PersistentAgentMessage, \
    PersistentAgentConversationParticipant, BrowserUseAgentTask, TaskCredit, PersistentAgentSmsEndpoint

from api.models import (
    ApiKey,
    UserBilling,
    PersistentAgent,
    BrowserUseAgent,
    PersistentAgentCommsEndpoint,
    PersistentAgentEmailEndpoint,
    CommsChannel,
    UserPhoneNumber,
    Organization,
    OrganizationMembership,
    OrganizationInvite,
)
from console.mixins import ConsoleViewMixin
from observability import traced
from pages.mixins import PhoneNumberMixin

from .context_helpers import build_console_context
from .org_billing_helpers import build_org_billing_overview
from tasks.services import TaskCreditService
from util import sms
from util.payments_helper import PaymentsHelper
from util.sms import find_unused_number, get_user_primary_sms_number
from util.subscription_helper import get_user_plan, get_active_subscription, allow_user_extra_tasks, \
    calculate_extra_tasks_used_during_subscription_period, get_user_extra_task_limit, get_or_create_stripe_customer
from config import settings
from config.stripe_config import get_stripe_settings

from .forms import (
    ApiKeyForm,
    PersistentAgentForm,
    PersistentAgentContactForm,
    UserProfileForm,
    UserPhoneNumberForm,
    PhoneVerifyForm,
    PhoneAddForm,
    OrganizationForm,
    OrganizationInviteForm,
    OrganizationSeatPurchaseForm,
)
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_http_methods
from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from django.core.paginator import Paginator
from waffle.mixins import WaffleFlagMixin
from constants.feature_flags import ORGANIZATIONS
from constants.grant_types import GrantTypeChoices
from constants.plans import PlanNamesChoices
from agent_namer import AgentNameGenerator
from opentelemetry import trace, baggage, context
from api.agent.tools.mcp_manager import enable_mcp_tool
from api.agent.tasks import process_agent_events_task
from console.forms import PersistentAgentEditSecretForm, PersistentAgentSecretsRequestForm, PersistentAgentAddSecretForm
import logging
from api.agent.comms.message_service import _get_or_create_conversation, _ensure_participant
from api.models import CommsAllowlistEntry, AgentAllowlistInvite, OrganizationMembership
from console.forms import AllowlistEntryForm
from console.forms import AgentEmailAccountConsoleForm
from django.apps import apps

User = get_user_model()
logger = logging.getLogger(__name__)

tracer = trace.get_tracer("gobii.utils")

# Whether to skip the phone number setup screen when the user already has a
# verified phone number on their account. Toggle this to force showing the
# phone screen even when a verified number exists.
SKIP_VERIFIED_SMS_SCREEN = True

BILLING_MANAGE_ROLES = {
    OrganizationMembership.OrgRole.OWNER,
    OrganizationMembership.OrgRole.ADMIN,
    OrganizationMembership.OrgRole.BILLING,
}

class ConsoleHome(ConsoleViewMixin, TemplateView):
    """Dashboard homepage for the console."""
    template_name = "index.html"

    @tracer.start_as_current_span("CONSOLE Home")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get the oldest non-revoked API key that has a raw key value
        default_key = ApiKey.objects.filter(
            user=self.request.user,
            revoked_at__isnull=True,
            raw_key__isnull=False
        ).exclude(
            raw_key=""
        ).order_by('created_at').first()

        if default_key and default_key.raw_key:
            context['default_api_key'] = default_key.raw_key
            context['has_api_key'] = True
        else:
            context['has_api_key'] = False

        # Add agent statistics (personal vs organization)
        from api.models import BrowserUseAgent, BrowserUseAgentTask

        current_ctx = context.get('current_context', {})
        ctx_type = current_ctx.get('type', 'personal')

        if ctx_type == 'organization' and current_ctx.get('id'):
            org_id = current_ctx.get('id')
            # Verify active membership; if missing, fall back to personal context values
            if OrganizationMembership.objects.filter(
                user=self.request.user,
                org_id=org_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists():
                # Agents (org-owned persistent agents)
                context['agent_count'] = PersistentAgent.objects.filter(organization_id=org_id).count()

                # Task status for org-owned agents
                from django.db.models import Count, Sum
                pa_browser_ids = (
                    PersistentAgent.objects.filter(organization_id=org_id)
                    .values_list('browser_use_agent_id', flat=True)
                )
                task_stats = (
                    BrowserUseAgentTask.objects.filter(
                        agent_id__in=pa_browser_ids,
                        is_deleted=False,
                    )
                    .values('status')
                    .annotate(count=Count('status'))
                )

                # Initialize counters
                completed_count = in_progress_count = pending_count = failed_count = cancelled_count = 0
                for stat in task_stats:
                    status = stat['status']
                    count = stat['count']
                    if status == 'completed':
                        completed_count = count
                    elif status == 'in_progress':
                        in_progress_count = count
                    elif status == 'pending':
                        pending_count = count
                    elif status == 'failed':
                        failed_count = count
                    elif status == 'cancelled':
                        cancelled_count = count

                context['completed_tasks'] = completed_count
                context['in_progress_tasks'] = in_progress_count
                context['pending_tasks'] = pending_count
                context['failed_tasks'] = failed_count
                context['cancelled_tasks'] = cancelled_count
                context['total_active_tasks'] = in_progress_count + pending_count

                # Credits available for organization
                from django.apps import apps
                TaskCredit = apps.get_model('api', 'TaskCredit')
                now = timezone.now()
                qs = TaskCredit.objects.filter(
                    organization_id=org_id,
                    granted_date__lte=now,
                    expiration_date__gte=now,
                    voided=False,
                )
                agg = qs.aggregate(
                    avail=Sum('available_credits'),
                    total=Sum('credits'),
                    used=Sum('credits_used'),
                )

                def _to_decimal(value):
                    if value is None:
                        return Decimal("0")
                    return value if isinstance(value, Decimal) else Decimal(value)

                org_tasks_available = agg['avail'] if agg['avail'] is not None else Decimal("0")
                total = _to_decimal(agg['total'])
                used = _to_decimal(agg['used'])

                if total == 0:
                    tasks_used_pct = Decimal("0")
                else:
                    usage_pct = (used / total) * Decimal("100")
                    tasks_used_pct = min(usage_pct, Decimal("100"))

                tasks_used_pct = float(tasks_used_pct)

                # Expose org metrics for dashboard rendering
                context['org_tasks_available'] = org_tasks_available
                context['org_tasks_used_pct'] = tasks_used_pct
            else:
                # Fallback to personal if no membership
                context['agent_count'] = BrowserUseAgent.objects.filter(user=self.request.user).count()
        else:
            # Personal context defaults
            agent_count = BrowserUseAgent.objects.filter(user=self.request.user).count()
            context['agent_count'] = agent_count

        # Get the user's subscription plan (defaults to 'free' if not set)
        context['subscription_plan'] = get_user_plan(self.request.user)

        # Get number of available tasks
        context['available_tasks'] = TaskCreditService.calculate_available_tasks(self.request.user)

        context['addl_tasks_enabled'] = allow_user_extra_tasks(self.request.user)
        context['addl_tasks_used'] = calculate_extra_tasks_used_during_subscription_period(self.request.user)
        context['addl_tasks_max'] = get_user_extra_task_limit(self.request.user)
        context['addl_tasks_unlimited'] = context['addl_tasks_max'] == -1  # -1 indicates unlimited tasks
        context['addl_tasks_remaining'] = context['addl_tasks_max'] - context['addl_tasks_used']

        # If enabled but not unlimited calculate percent. else 0
        if context['addl_tasks_enabled'] and not context['addl_tasks_unlimited']:
            context['addl_tasks_percent'] = min(max((context['addl_tasks_used'] / context['addl_tasks_max'] * 100), 0), 100)
        else:
            context['addl_tasks_percent'] = 0

        # If they have query parameter subscribe_success=1, put `subscribe_notification` as true in context for tpl use
        if self.request.GET.get('subscribe_success') == '1':
            context['subscribe_notification'] = True
            price_str = self.request.GET.get('p', '0.0')
            try:
                # Ensure sub_price is a valid number to prevent XSS and ensure correct tracking.
                context['sub_price'] = float(price_str)
            except ValueError:
                context['sub_price'] = 0.0
        else:
            context['subscribe_notification'] = False


        # Get the user's active subscription
        sub = get_active_subscription(self.request.user)
        context['subscription'] = sub
        context['paid_subscriber'] = sub is not None

        if sub:
            start = sub.stripe_data['current_period_start']
            end = sub.stripe_data['current_period_end']

            dt_start = datetime.fromtimestamp(int(start), tz=dt_timezone.utc)
            dt_end = datetime.fromtimestamp(int(end), tz=dt_timezone.utc)

            context['period_start_date'] = dt_start.strftime("%B %d, %Y")
            context['period_end_date'] = dt_end.strftime("%B %d, %Y")

        # Get task status breakdown
        from django.db.models import Count

        # If not in org context above, compute personal task stats
        if not (ctx_type == 'organization' and current_ctx.get('id')):
            with traced("CONSOLE Task Stats") as task_span:
                from django.db.models import Count
                task_stats = BrowserUseAgentTask.objects.filter(
                    user=self.request.user,
                    is_deleted=False
                ).values('status').annotate(count=Count('status'))

                # Initialize counters
                completed_count = in_progress_count = pending_count = failed_count = cancelled_count = 0

                # Populate counters from query results
                for stat in task_stats:
                    status = stat['status']
                    count = stat['count']
                    if status == 'completed':
                        completed_count = count
                    elif status == 'in_progress':
                        in_progress_count = count
                    elif status == 'pending':
                        pending_count = count
                    elif status == 'failed':
                        failed_count = count
                    elif status == 'cancelled':
                        cancelled_count = count

                # Add task statistics to context
                context['completed_tasks'] = completed_count
                context['in_progress_tasks'] = in_progress_count
                context['pending_tasks'] = pending_count
                context['failed_tasks'] = failed_count
                context['cancelled_tasks'] = cancelled_count
                context['total_active_tasks'] = in_progress_count + pending_count

        return context

class ExampleConsolePage(LoginRequiredMixin, TemplateView):
    """Example console page."""
    template_name = "example_console_page.html"

class ApiKeyListView(ConsoleViewMixin, FormMixin, ListView):
    """List all API keys for the current user and handle creation."""
    model = ApiKey
    template_name = "api_keys.html"
    context_object_name = 'api_keys'
    form_class = ApiKeyForm
    success_url = reverse_lazy('api_keys')

    @tracer.start_as_current_span("CONSOLE API Key List - get_queryset")
    def get_queryset(self):
        return ApiKey.objects.filter(user=self.request.user).order_by('-created_at')

    @tracer.start_as_current_span("CONSOLE API Key List - get_context_data")
    def get_context_data(self, **kwargs):
        """Add form to context."""
        context = super().get_context_data(**kwargs)
        context['form'] = self.get_form() # Add form instance from FormMixin
        return context

    @tracer.start_as_current_span("CONSOLE API Key List - get_form_kwargs")
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    @tracer.start_as_current_span("CONSOLE API Key List - Create API Key")
    def post(self, request, *args, **kwargs):
        """Handle POST requests for creating a new API key."""
        # Check if user is authenticated (redundant due to LoginRequiredMixin, but good practice)
        if not request.user.is_authenticated:
            return HttpResponseForbidden()

        form = self.get_form()
        if form.is_valid():
            try:
                return self.form_valid(form)
            except ValidationError as e:
                # Extract the actual error message from the ValidationError
                # ValidationError can be a dict, list, or string
                if hasattr(e, 'message_dict'):
                    # Get the first error from the '__all__' key if it exists
                    error_message = e.message_dict.get('__all__', ['An error occurred'])[0]
                elif hasattr(e, 'messages'):
                    error_message = e.messages[0]
                else:
                    error_message = str(e)
                
                # Add the clean error message to the form
                form.add_error(None, error_message)
                
                # Re-render the form with errors
                if request.htmx:
                    # On validation error, re-render the form and swap it in place
                    # This maintains the original behavior
                    response = render(request, "partials/_api_key_form.html", {"form": form})
                    response["HX-Retarget"] = "#create-api-key-form"
                    response['HX-Reswap'] = 'outerHTML'
                    return response
                else:
                    self.object_list = self.get_queryset()
                    return self.render_to_response(self.get_context_data(form=form))
        else:
            # If form is invalid, return the modal with errors for HTMX
            if request.htmx:
                # On validation error, re-render the form and swap it in place
                response = render(request, "partials/_api_key_form.html", {"form": form})
                response["HX-Retarget"] = "#create-api-key-form"
                response['HX-Reswap'] = 'outerHTML'
                return response
            else:
                # ListView doesn't have form_invalid, so we manually call get()
                # to reconstruct the context including the invalid form.
                self.object_list = self.get_queryset() # Need to set this for get()
                return self.render_to_response(self.get_context_data(form=form))

    @transaction.atomic
    def form_valid(self, form):
        """Process a valid form to create an API key."""
        name = form.cleaned_data['name']
        # create_for_user bypasses model validation by using objects.create
        # The validation will now happen in the model's save method
        # which could raise ValidationError (e.g., if key limit is reached)
        raw_key, api_key = ApiKey.create_for_user(self.request.user, name=name)

        transaction.on_commit(lambda : Analytics.track_event(
            user_id=self.request.user.id,
            event=AnalyticsEvent.API_KEY_CREATED,
            source=AnalyticsSource.WEB,
            properties={
                'key_id': str(api_key.id),
                'key_name': name,
            }
        ))

        if self.request.htmx:
            # Return the newly created API key notification for HTMX
            response = render(self.request, "partials/_api_key_created.html", {
                "raw_key": raw_key,
                "key_id": api_key.id
            })
            # Trigger events to refresh the table and close the modal
            response["HX-Trigger"] = json.dumps({
                "refreshApiKeysTable": None,
                "close-modal": {"id": "create-api-key-modal"},
            })
            return response
        else:
            # Traditional flow with message and redirect
            messages.success(
                self.request,
                f"New API key created: {raw_key}. Copy this key now, you won't be able to see it again!"
            )
            return redirect(self.get_success_url())


class ApiKeyDetailView(LoginRequiredMixin, View):
    """Handle Revoke (PATCH) and Delete (DELETE) for a specific API key."""
    http_method_names = ['get', 'patch', 'delete', 'options'] # Added GET for HTMX refresh

    @tracer.start_as_current_span("API Key Get Object")
    def get_object(self):
        """Helper to get the API key or raise 404."""
        return get_object_or_404(
            ApiKey,
            id=self.kwargs['pk'],
            user=self.request.user # Ensure user owns the key
        )

    @tracer.start_as_current_span("API Key Detail View - GET")
    def get(self, request, *args, **kwargs):
        """Handle GET requests to refresh a row via HTMX."""
        if not request.htmx:
            # If not HTMX, redirect to the list view
            return redirect(reverse('api_keys'))
            
        # Get the API key and render just the row
        api_key = self.get_object()

        # Not tracking here as it's a small segment of larger page

        return render(request, "partials/_api_key_row.html", {"key": api_key})

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        """Handle PATCH requests to revoke an API key."""
        api_key = self.get_object()
        api_key.revoke()

        transaction.on_commit(lambda : Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.API_KEY_REVOKED,
            source=AnalyticsSource.WEB,
            properties={
                'key_id': str(api_key.id),
                'key_name': api_key.name,
            }
        ))
        
        if request.htmx:
            # First return success message
            response = render(request, "partials/_api_key_success.html", {
                "message": f"API key '{api_key.name}' has been revoked.",
                "id": api_key.id
            })
            # Set HX-Trigger to refresh the table row
            response["HX-Trigger"] = f"refresh-row-{api_key.id}"
            return response
        else:
            # Traditional response with message and redirect
            messages.success(request, f"API key '{api_key.name}' has been revoked.")
            return redirect(reverse('api_keys'))


    @transaction.atomic
    def delete(self, request, *args, **kwargs):
        """Handle DELETE requests to permanently delete an API key."""
        api_key = self.get_object()
        key_name = api_key.name # Store name before deleting
        key_id = api_key.id     # Store ID before deleting
        api_key.delete()

        transaction.on_commit(lambda : Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.API_KEY_DELETED,
            source=AnalyticsSource.WEB,
            properties={
                'key_id': str(key_id),
                'key_name': key_name,
            }
        ))
        
        if request.htmx:
            # Render the success message partial
            response = render(request, "partials/_api_key_deleted_message.html", {"key_name": key_name})
            # Trigger table refresh and modal close
            response['HX-Trigger'] = '{"refreshApiKeysTable": null, "closeDeleteModal": null}'

            return response
        else:
            # Traditional response
            messages.success(request, f"API key '{key_name}' has been permanently deleted.")
            return redirect(reverse('api_keys'))

    def http_method_not_allowed(self, request, *args, **kwargs):
        """Handle disallowed methods."""
        # Log or handle the error as needed
        return HttpResponseNotAllowed(self._allowed_methods())

class ApiKeyTableView(LoginRequiredMixin, ListView):
    model = ApiKey
    template_name = "partials/_api_key_table_body.html"  # New partial for just the table body
    context_object_name = "api_keys"

    @tracer.start_as_current_span("API Key Table View - GET")
    def get_queryset(self):
        # Return keys ordered by creation date, newest first
        return ApiKey.objects.filter(user=self.request.user).order_by('-created_at')

class ApiKeyBlankFormView(LoginRequiredMixin, View):
    @tracer.start_as_current_span("API Key Blank Form View - GET")
    def get(self, request, *args, **kwargs):
        form = ApiKeyForm(user=request.user)
        return render(request, "partials/_api_key_form.html", {"form": form})

class ApiKeyCreateModalView(LoginRequiredMixin, View):
    @tracer.start_as_current_span("API Key Create Modal View - GET")
    def get(self, request, *args, **kwargs):
        form = ApiKeyForm(user=request.user)
        return render(request, "partials/_api_key_modal.html", {"form": form})

class BillingView(ConsoleViewMixin, TemplateView):
    """View for billing information."""
    template_name = "billing.html"

    @tracer.start_as_current_span("CONSOLE Billing View")
    def get(self, request, *args, **kwargs):
        context = super().get_context_data(**kwargs)

        current_context = context.get('current_context', {}) or {}
        if current_context.get('type') == 'organization' and current_context.get('id'):
            try:
                organization = Organization.objects.select_related('billing').get(id=current_context['id'])
            except Organization.DoesNotExist:
                messages.error(request, 'Organization not found. Switching back to personal billing.')
                request.session['context_type'] = 'personal'
                request.session['context_id'] = str(request.user.id)
                request.session['context_name'] = request.user.get_full_name() or request.user.email
                return redirect('billing')
            else:
                overview = build_org_billing_overview(organization)
                membership = context.get('current_membership')
                allowed_roles = {
                    OrganizationMembership.OrgRole.OWNER,
                    OrganizationMembership.OrgRole.ADMIN,
                    OrganizationMembership.OrgRole.BILLING,
                }
                can_manage_billing = bool(membership and membership.role in allowed_roles)

                configured_limit = overview['extra_tasks']['configured_limit'] or 0
                auto_purchase_state = {
                    'enabled': configured_limit not in (0,),
                    'infinite': configured_limit == -1,
                    'max_tasks': configured_limit if configured_limit not in (0, -1) else 1000,
                }

                granted = Decimal(str(overview['credits']['granted'])) if overview['credits']['granted'] else Decimal('0')
                used = Decimal(str(overview['credits']['used'])) if overview['credits']['used'] else Decimal('0')
                usage_pct = 0
                if granted > 0:
                    usage_pct = min(100, float((used / granted) * 100))

                context.update({
                    'organization': organization,
                    'org_billing_overview': overview,
                    'org_can_manage_billing': can_manage_billing,
                    'org_auto_purchase_state': auto_purchase_state,
                    'org_credit_usage_pct': usage_pct,
                    'org_can_open_stripe': can_manage_billing and bool(overview['billing_record']['stripe_customer_id']),
                })
                return render(request, self.template_name, context)

        # Personal billing fallback
        context['subscription_plan'] = get_user_plan(self.request.user)
        sub = get_active_subscription(self.request.user)
        paid_subscriber = sub is not None

        if sub:
            start = sub.stripe_data['current_period_start']
            end = sub.stripe_data['current_period_end']
            cancel_at = getattr(sub.stripe_data, "cancel_at", None)

            dt_start = datetime.fromtimestamp(int(start), tz=dt_timezone.utc)
            dt_end = datetime.fromtimestamp(int(end), tz=dt_timezone.utc)
            dt_cancel_at = datetime.fromtimestamp(int(cancel_at), tz=dt_timezone.utc) if cancel_at else None


            context['period_start_date'] = dt_start.strftime("%B %d, %Y")
            context['period_end_date'] = dt_end.strftime("%B %d, %Y")
            context['subscription_active'] = sub.is_status_current()
            context['cancel_at'] = dt_cancel_at
            context['cancel_at_period_end'] = getattr(sub.stripe_data, "cancel_at_period_end", False)

        context['subscription'] = sub
        context['paid_subscriber'] = paid_subscriber

        return render(request, self.template_name, context)

    @tracer.start_as_current_span("CONSOLE Billing Post (not allowed)")
    def post(self, request, *args, **kwargs):
        # Handle any POST requests related to billing here
        return HttpResponseNotAllowed(['GET'])


class ProfileView(ConsoleViewMixin, PhoneNumberMixin, TemplateView):
    """Allow users to manage basic profile information and phone number."""

    template_name = "console/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["profile_form"] = UserProfileForm(instance=user)

        return context

    def post(self, request, *args, **kwargs):
        """
        • PhoneNumberMixin handles add / verify / delete.
          If it returns an HttpResponse, we’re done.
        • Otherwise we process the normal profile form.
        •   If that fails validation, re-render the page with errors.
        """

        # 1️⃣ phone-related actions (HTMX or regular) ------------------------
        resp = self._handle_phone_post()  # provided by the mixin
        if resp is not None:  # mixin already produced a response
            return resp

        # 2️⃣ profile form ---------------------------------------------------
        profile_form = UserProfileForm(request.POST, instance=request.user)
        if profile_form.is_valid():
            profile_form.save()
            return redirect("profile")

        # 3️⃣ invalid profile form → rebuild full context --------------------
        context = self.get_context_data()
        context["profile_form"] = profile_form  # include bound form with errors
        return self.render_to_response(context)

@login_required
@require_POST
@transaction.atomic
@tracer.start_as_current_span("BILLING Update Billing Settings")
def update_billing_settings(request):
    try:
        data = json.loads(request.body)
        auto_purchase = data.get('enabled', False)
        infinite = data.get('infinite', False)
        max_tasks = data.get('maxTasks', 5)
        resolved = build_console_context(request)

        if resolved.current_context.type == 'organization' and resolved.current_membership:
            membership = resolved.current_membership
            if membership.role not in BILLING_MANAGE_ROLES:
                return JsonResponse({'success': False, 'error': 'Not permitted'}, status=403)

            OrgBilling = apps.get_model('api', 'OrganizationBilling')
            defaults = {'max_extra_tasks': 0, 'billing_cycle_anchor': timezone.now().day}
            org_billing, _ = OrgBilling.objects.get_or_create(
                organization=membership.org,
                defaults=defaults,
            )

            if not auto_purchase:
                org_billing.max_extra_tasks = 0
            elif infinite:
                org_billing.max_extra_tasks = -1
            else:
                org_billing.max_extra_tasks = max(1, int(max_tasks))

            org_billing.save(update_fields=['max_extra_tasks', 'updated_at'])

            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.BILLING_UPDATED,
                source=AnalyticsSource.WEB,
                properties={
                    'max_extra_tasks': org_billing.max_extra_tasks,
                    'auto_purchase': auto_purchase,
                    'infinite': infinite,
                    'owner_type': 'organization',
                    'organization_id': str(membership.org.id),
                }
            ))

            return JsonResponse({
                'success': True,
                'max_extra_tasks': org_billing.max_extra_tasks,
                'owner_type': 'organization',
            })

        user_billing, _ = UserBilling.objects.get_or_create(
            user=request.user,
            defaults={'max_extra_tasks': 0}
        )

        if not auto_purchase:
            user_billing.max_extra_tasks = 0
        elif infinite:
            user_billing.max_extra_tasks = -1
        else:
            user_billing.max_extra_tasks = max(1, int(max_tasks))

        user_billing.save(update_fields=['max_extra_tasks'])

        transaction.on_commit(lambda: Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.BILLING_UPDATED,
            source=AnalyticsSource.WEB,
            properties={
                'max_extra_tasks': user_billing.max_extra_tasks,
                'auto_purchase': auto_purchase,
                'infinite': infinite,
                'owner_type': 'user',
            }
        ))

        return JsonResponse({
            'success': True,
            'max_extra_tasks': user_billing.max_extra_tasks,
            'owner_type': 'user',
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)

@login_required
@tracer.start_as_current_span("BILLING Get Billing Settings")
def get_billing_settings(request):
    try:
        resolved = build_console_context(request)

        if resolved.current_context.type == 'organization' and resolved.current_membership:
            membership = resolved.current_membership
            if membership.role not in BILLING_MANAGE_ROLES and membership is not None:
                # Allow read-only access even without manage role, but disable editing client side
                permitted = False
            else:
                permitted = True

            OrgBilling = apps.get_model('api', 'OrganizationBilling')
            defaults = {'max_extra_tasks': 0, 'billing_cycle_anchor': timezone.now().day}
            org_billing, _ = OrgBilling.objects.get_or_create(
                organization=membership.org,
                defaults=defaults,
            )

            return JsonResponse({
                'max_extra_tasks': org_billing.max_extra_tasks,
                'owner_type': 'organization',
                'can_modify': permitted,
            })

        user_billing, _ = UserBilling.objects.get_or_create(
            user=request.user,
            defaults={'max_extra_tasks': 0}
        )

        return JsonResponse({
            'max_extra_tasks': user_billing.max_extra_tasks,
            'owner_type': 'user',
            'can_modify': True,
        })
    except Exception as e:
        return JsonResponse({
            'error': str(e)
        }, status=400)

@login_required
@require_POST
@tracer.start_as_current_span("BILLING Cancel Subscription")
def cancel_subscription(request):
    """Endpoint to cancel the user's subscription."""
    sub = get_active_subscription(request.user)
    if sub:
        try:
            stripe.api_key = PaymentsHelper.get_stripe_key()
            stripe.Subscription.modify(sub.id, cancel_at_period_end=True)

            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.BILLING_CANCELLATION,
                source=AnalyticsSource.WEB,
                properties={},
            )

            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({
                    'success': False,
                    'error': 'Error cancelling subscription'
                },
                status=500)
    else:
        return JsonResponse({
            'success': False,
            'error': "You do not have an active subscription to cancel."
        }, status=400)

@login_required
def tasks_view(request):
    # Get current context from session
    context_type = request.session.get('context_type', 'personal')
    context_id = request.session.get('context_id', str(request.user.id))
    
    # Get tasks for the current context
    with traced("CONSOLE Tasks View") as span:
        if context_type == 'organization':
            # Ensure the requester is an active member of the organization context
            if not OrganizationMembership.objects.filter(
                user=request.user,
                org_id=context_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists():
                return HttpResponseForbidden("You do not have access to this organization.")
            # For organization context, show tasks from agents owned by the organization
            # Get BrowserUseAgents that are linked to PersistentAgents in this organization
            persistent_agent_ids = PersistentAgent.objects.filter(
                organization_id=context_id
            ).values_list('browser_use_agent_id', flat=True)
            tasks_queryset = BrowserUseAgentTask.objects.filter(
                agent_id__in=persistent_agent_ids,
                is_deleted=False
            ).order_by('-created_at')
        else:
            # For personal context, show user's personal tasks only
            # Exclude tasks for org-owned agents; include agent-less tasks
            tasks_queryset = (
                BrowserUseAgentTask.objects.filter(
                    user=request.user,
                    is_deleted=False
                )
                .exclude(agent__persistent_agent__organization__isnull=False)
                .order_by('-created_at')
            )

        # Handle filtering by status
        status_filter = request.GET.get('status')
        if status_filter:
            span.set_attribute('tasks.status_filter', status_filter)
            tasks_queryset = tasks_queryset.filter(status=status_filter)

        # Handle search
        search_query = request.GET.get('search')
        if search_query:
            span.set_attribute('tasks.search_query', search_query)
            tasks_queryset = tasks_queryset.filter(prompt__icontains=search_query)

        # Pagination
        paginator = Paginator(tasks_queryset, 10)  # Show 10 tasks per page
        page_number = request.GET.get('page', 1)

        with traced("CONSOLE Tasks View Pagination") as span:
            span.set_attribute('tasks.page_number', page_number)
            tasks = paginator.get_page(page_number)

        # Get user's organization memberships for context switcher
        user_organizations = OrganizationMembership.objects.filter(
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE
        ).select_related('org').order_by('org__name')
        
        context = {
            'tasks': tasks,
            'status_filter': status_filter,
            'user_organizations': user_organizations,
            'current_context': {
                'type': context_type,
                'id': context_id,
                'name': request.session.get('context_name', request.user.get_full_name() or request.user.username)
            }
        }
        
        return render(request, 'tasks.html', context)

@login_required
def task_detail_view(request, task_id):
    # Get the task with related steps
    with traced("CONSOLE Task Detail View") as span:
        span.set_attribute('task.id', str(task_id))
        with traced("CONSOLE Task Detail Fetch Task"):
            task = get_object_or_404(
                BrowserUseAgentTask.objects.prefetch_related('steps'),
                id=task_id,
                user=request.user,
                is_deleted=False
            )

        return render(request, 'task_detail.html', {'task': task})

@login_required
def task_cancel_view(request, task_id):
    if request.method == 'POST':
        with traced("CONSOLE Task Cancel", user_id=request.user.id) as span:
            # Get the task
            task = get_object_or_404(
                BrowserUseAgentTask,
                id=task_id,
                user=request.user,
                is_deleted=False
            )

            # Only allow cancelling tasks that are pending or in_progress
            if task.status in [BrowserUseAgentTask.StatusChoices.PENDING, BrowserUseAgentTask.StatusChoices.IN_PROGRESS]:
                # Update task status
                task.status = BrowserUseAgentTask.StatusChoices.CANCELLED
                task.save()

                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.WEB_TASK_CANCELLED,
                    source=AnalyticsSource.WEB,
                    properties={
                        'task_id': str(task.id),
                        'task_status': task.status
                    }
                )

                messages.success(request, "Task successfully cancelled.")
            else:
                messages.error(request, "This task cannot be cancelled.")

            return redirect('task_detail', task_id=task_id)

    # If not POST, redirect to task detail
    return redirect('task_detail', task_id=task_id)

@login_required
@tracer.start_as_current_span("CONSOLE Task Result View")
def task_result_view(request, task_id):
    # Get the task
    span = trace.get_current_span()
    span.set_attribute('task.id', str(task_id))
    span.set_attribute('user.id', str(request.user.id))
    with traced("CONSOLE Task Result Fetch Task"):
        task = get_object_or_404(
            BrowserUseAgentTask.objects.prefetch_related('steps'),
            id=task_id,
            user=request.user,
            is_deleted=False
        )

    span.set_attribute('task.status', task.status)

    # Ensure the task is completed
    if task.status != BrowserUseAgentTask.StatusChoices.COMPLETED:
        messages.error(request, "Task result is not available yet.")
        return redirect('task_detail', task_id=task_id)

    # Find the result step
    with traced("CONSOLE Task Result Fetch Step"):
        result_step = task.steps.filter(is_result=True).first()

    # Handle JSON download format
    if request.GET.get('format') == 'json' and result_step and result_step.result_value:
        # if result_step.result_value is a string, parse it as JSON
        response = None

        # Some shenanigans to handle both JSON and invalid JSON gracefully (send as text if invalid)
        try:
            response = JsonResponse(result_step.result_value)
            span.set_attribute('task.result_format', 'json')
            response['Content-Disposition'] = f'attachment; filename="task_{task_id}_result.json"'
        except TypeError:
            span.set_attribute('task.result_format', 'text')
            response = HttpResponse(result_step.result_value, content_type='text/plain; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="task_{task_id}_result.txt"'

        # Track the download event
        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.WEB_TASK_RESULT_DOWNLOADED,
            source=AnalyticsSource.WEB,
            properties={
                'task_id': str(task.id),
                'task_status': task.status,
                'result_step_id': str(result_step.id)
            }
        )

        return response

    span.set_attribute('task.result_format', 'html')

    # For regular HTML rendering
    import json
    context = {
        'task': task,
        'result_step': result_step,
    }

    return render(request, 'task_result.html', context)

# ────────── Persistent Agents (Feature-Flagged) ──────────
class PersistentAgentsView(ConsoleViewMixin, TemplateView):
    template_name = "console/persistent_agents.html"

    @tracer.start_as_current_span("CONSOLE Persistent Agents View")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Define a prefetch for the primary email endpoint to avoid N+1 queries
        primary_email_prefetch = models.Prefetch(
            'comms_endpoints',
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.EMAIL, is_primary=True),
            to_attr='primary_email_endpoints'  # Use a plural name as it's a list
        )

        primary_sms_prefetch = models.Prefetch(
            'comms_endpoints',
            queryset=PersistentAgentCommsEndpoint.objects.filter(channel=CommsChannel.SMS, is_primary=True),
            to_attr='primary_sms_endpoints'  # Use a plural name as it's a list
        )

        # Filter agents based on current context
        current_context = context.get('current_context', {})
        if current_context.get('type') == 'organization':
            # Show organization's agents
            persistent_agents = PersistentAgent.objects.filter(
                organization_id=current_context.get('id')
            ).select_related('browser_use_agent').prefetch_related(primary_email_prefetch).prefetch_related(primary_sms_prefetch).order_by('-created_at')
        else:
            # Show personal agents
            persistent_agents = PersistentAgent.objects.filter(
                user=self.request.user,
                organization__isnull=True  # Only personal agents
            ).select_related('browser_use_agent').prefetch_related(primary_email_prefetch).prefetch_related(primary_sms_prefetch).order_by('-created_at')
        
        context['persistent_agents'] = persistent_agents

        context['has_agents'] = persistent_agents.exists()

        return context


class AgentCreateContactView(ConsoleViewMixin, PhoneNumberMixin, TemplateView):
    """Step 2: Contact preferences for agent creation."""
    template_name = "console/agent_create_contact.html"

    @tracer.start_as_current_span("CONSOLE Agent Create Contact View")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Pre-populate with user's email and SMS if verified
        if 'form' not in kwargs:
            initial_data = {'contact_endpoint_email': self.request.user.email}

            template_code = self.request.session.get(AIEmployeeTemplateService.TEMPLATE_SESSION_KEY)
            template = AIEmployeeTemplateService.get_template_by_code(template_code) if template_code else None

            if template:
                template.schedule_description = AIEmployeeTemplateService.describe_schedule(template.base_schedule)
                template.display_default_tools = AIEmployeeTemplateService.get_tool_display_list(
                    template.default_tools or []
                )
                template.contact_method_label = AIEmployeeTemplateService.describe_contact_channel(
                    template.recommended_contact_channel
                )
                context['selected_ai_employee'] = template
                preferred = (template.recommended_contact_channel or '').lower()
                valid_choices = {choice for choice, _ in PersistentAgentContactForm.CONTACT_METHOD_CHOICES}
                if preferred in valid_choices:
                    initial_data['preferred_contact_method'] = preferred

            context['form'] = PersistentAgentContactForm(initial=initial_data)
        else:
            template_code = self.request.session.get(AIEmployeeTemplateService.TEMPLATE_SESSION_KEY)
            template = AIEmployeeTemplateService.get_template_by_code(template_code) if template_code else None
            if template:
                template.schedule_description = AIEmployeeTemplateService.describe_schedule(template.base_schedule)
                template.display_default_tools = AIEmployeeTemplateService.get_tool_display_list(
                    template.default_tools or []
                )
                template.contact_method_label = AIEmployeeTemplateService.describe_contact_channel(
                    template.recommended_contact_channel
                )
                context['selected_ai_employee'] = template

        current_context = context.get('current_context', {
            'type': 'personal',
            'name': self.request.user.get_full_name() or self.request.user.username,
        })

        if current_context.get('type') == 'organization':
            context['agent_owner_label'] = current_context.get('name')
        else:
            context['agent_owner_label'] = self.request.user.get_full_name() or self.request.user.username

        context.setdefault('can_manage_org_agents', True)
        context['show_org_permission_warning'] = (
            current_context.get('type') == 'organization' and not context['can_manage_org_agents']
        )

        return context

    def get(self, request, *args, **kwargs):
        """Render the contact preferences form."""
        if not AgentService.has_agents_available(request.user):
            messages.error(request, "You do not have any persistent agents available. Please upgrade to spawn more.")
            return redirect('pages:home')

        # Check if we have charter data from step 1
        if 'agent_charter' not in self.request.session:
            messages.error(self.request, "Please start by describing what your agent should do.")
            return redirect('agents')

        return self.render_to_response(self.get_context_data())

    @tracer.start_as_current_span("CONSOLE Agent Create Contact - Create Agent")
    def post(self, request, *args, **kwargs):
        """Handle step 2: create the agent with contact preferences."""
        
        # Import here to avoid circular import during Django startup
        from api.agent.comms.message_service import _get_or_create_conversation, _ensure_participant

        resp = self._handle_phone_post()
        if resp:  # phone add/verify/delete handled
            return resp

        form = PersistentAgentContactForm(request.POST)
        phone = self._current_phone()  # helper from PhoneNumberMixin

        if form.is_valid():
            if form.cleaned_data['preferred_contact_method'] == 'sms' and (
                    not phone or not phone.is_verified):
                form.add_error(None, "Please verify a phone number before selecting SMS.")

        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        # Check if we have charter data from step 1
        if 'agent_charter' not in request.session:
            messages.error(request, "Please start by describing what your agent should do.")
            return redirect('agents')
        
        form = PersistentAgentContactForm(request.POST)

        if form.is_valid():
            initial_user_message = request.session.get('agent_charter')
            user_contact_email = form.cleaned_data['contact_endpoint_email']
            user_contact_sms = None
            sms_enabled = form.cleaned_data.get('sms_enabled', False)
            email_enabled = form.cleaned_data.get('email_enabled', False)
            preferred_contact_method = form.cleaned_data['preferred_contact_method']

            sms_preferred = preferred_contact_method == "sms"

            template_code = request.session.get(AIEmployeeTemplateService.TEMPLATE_SESSION_KEY)
            selected_template = AIEmployeeTemplateService.get_template_by_code(template_code) if template_code else None
            applied_schedule = None

            try:
                with transaction.atomic():
                    resolved_context = build_console_context(request)
                    organization = None
                    if resolved_context.current_context.type == 'organization':
                        membership = resolved_context.current_membership
                        if membership is None:
                            messages.error(
                                request,
                                "You no longer have access to that organization. Creating a personal agent instead.",
                            )
                        elif not resolved_context.can_manage_org_agents:
                            form.add_error(
                                None,
                                "You need to be an organization owner or admin to create agents for this organization.",
                            )
                            return self.render_to_response(self.get_context_data(form=form))
                        else:
                            organization = membership.org

                    # Generate a unique agent name after confirming permissions
                    agent_name = self._generate_unique_agent_name(request.user)

                    # Create the BrowserUseAgent first
                    browser_agent = BrowserUseAgent.objects.create(
                        user=request.user,
                        name=agent_name
                    )

                    # Then create the PersistentAgent with no initial charter
                    # The agent will set its own charter based on the user's message
                    persistent_agent = PersistentAgent.objects.create(
                        user=request.user,
                        organization=organization,  # Set organization if in org context
                        name=agent_name,
                        charter="",  # Empty charter - agent will set this itself
                        schedule=None,
                        browser_use_agent=browser_agent,
                        preferred_contact_endpoint=None  # Temporary until we set it below
                    )

                    if selected_template:
                        fields_to_update = []
                        if selected_template.charter:
                            persistent_agent.charter = selected_template.charter
                            fields_to_update.append("charter")

                        applied_schedule = AIEmployeeTemplateService.compute_schedule_with_jitter(
                            selected_template.base_schedule,
                            selected_template.schedule_jitter_minutes,
                        )

                        if applied_schedule:
                            persistent_agent.schedule = applied_schedule
                            persistent_agent.schedule_snapshot = selected_template.base_schedule
                            fields_to_update.extend(["schedule", "schedule_snapshot"])

                        if fields_to_update:
                            persistent_agent.save(update_fields=fields_to_update)
                    
                    # Generate a unique email for the agent itself
                    user_contact = None
                    user_email_comms_endpoint = None
                    user_sms_comms_endpoint = None

                    if sms_enabled:
                        user_primary_sms = get_user_primary_sms_number(user=request.user)
                        user_contact_sms = user_primary_sms.phone_number if user_primary_sms else None

                        if user_primary_sms is None:
                            messages.error(
                                request,
                                "You must have a verified phone number to create an agent with SMS contact."
                            )
                            return redirect('agents')

                        agent_sms = find_unused_number()

                        agent_comms_endpoint = PersistentAgentCommsEndpoint.objects.create(
                            owner_agent=persistent_agent,
                            channel=CommsChannel.SMS,
                            address=agent_sms.phone_number,
                            is_primary=preferred_contact_method == "sms",
                        )
                        PersistentAgentSmsEndpoint.objects.create(
                            endpoint=agent_comms_endpoint,
                            supports_mms=True,  # SMS endpoints support messages
                            carrier_name=agent_sms.provider
                        )

                        user_sms_comms_endpoint, created = PersistentAgentCommsEndpoint.objects.get_or_create(
                            channel=CommsChannel.SMS,
                            address__iexact=user_primary_sms.phone_number,
                            defaults={'address': user_primary_sms.phone_number, 'owner_agent': None}
                        )

                        user_contact = user_primary_sms.phone_number

                    if email_enabled:
                        from django.conf import settings as dj_settings
                        # Create agent-owned email endpoint only when enabled (Gobii proprietary mode)
                        if getattr(dj_settings, 'ENABLE_DEFAULT_AGENT_EMAIL', False):
                            # Generate a unique email for the agent
                            agent_email = self._generate_unique_agent_email(agent_name)

                            # Create the agent's OWN primary email endpoint (for receiving)
                            agent_comms_endpoint = PersistentAgentCommsEndpoint.objects.create(
                                owner_agent=persistent_agent,
                                channel=CommsChannel.EMAIL,
                                address=agent_email,
                                is_primary=preferred_contact_method == "email",
                            )
                            PersistentAgentEmailEndpoint.objects.create(
                                endpoint=agent_comms_endpoint,
                                display_name=agent_name,
                                verified=True,  # System-generated, so considered verified
                            )

                        # Always create the EXTERNAL endpoint for the user's contact address
                        user_email_comms_endpoint, created = PersistentAgentCommsEndpoint.objects.get_or_create(
                            channel=CommsChannel.EMAIL,
                            address__iexact=user_contact_email,
                            defaults={'address': user_contact_email, 'owner_agent': None}
                        )

                        user_contact = user_contact_email
                    
                    # Store the preferred contact endpoint on the agent
                    persistent_agent.preferred_contact_endpoint = user_sms_comms_endpoint if sms_preferred else user_email_comms_endpoint
                    persistent_agent.save(update_fields=["preferred_contact_endpoint"])

                    # Send regulatory SMS if SMS is enabled
                    if sms_enabled:
                        try:
                            sms.send_sms(
                                to_number=user_primary_sms.phone_number,
                                from_number=agent_sms.phone_number,
                                body="Gobii: You’ve enabled SMS communication with Gobii. Reply HELP for help, STOP to opt-out."
                            )
                        except Exception as e:
                            logger.error("Error sending initial SMS to user after agent creation: %s", str(e))

                    conversation = _get_or_create_conversation(
                        channel=CommsChannel.SMS.value if sms_preferred else CommsChannel.EMAIL.value,
                        address=user_contact,
                        owner_agent=persistent_agent
                    )

                    # Set up conversation participants
                    if user_sms_comms_endpoint:
                        _ensure_participant(conversation, user_sms_comms_endpoint, PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL)

                    if user_email_comms_endpoint:
                        _ensure_participant(conversation, user_email_comms_endpoint, PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL)

                    # Add agent participant if an agent-owned endpoint exists
                    try:
                        if 'agent_comms_endpoint' in locals() and agent_comms_endpoint is not None:
                            _ensure_participant(conversation, agent_comms_endpoint, PersistentAgentConversationParticipant.ParticipantRole.AGENT)
                    except Exception:
                        pass

                    # Create the initial message from user to agent
                    PersistentAgentMessage.objects.create(
                        is_outbound=False,  # Message from user to agent
                        from_endpoint=user_sms_comms_endpoint if sms_preferred else user_email_comms_endpoint,
                        conversation=conversation,
                        body=initial_user_message,
                        owner_agent=persistent_agent,
                    )

                    if selected_template and selected_template.default_tools:
                        for tool_name in selected_template.default_tools:
                            try:
                                enable_mcp_tool(persistent_agent, tool_name)
                            except Exception as exc:
                                logger.warning(
                                    "Failed to enable MCP tool '%s' for agent %s: %s",
                                    tool_name,
                                    persistent_agent.id,
                                    exc,
                                )

                    # Trigger the first event processing run after commit
                    transaction.on_commit(lambda: process_agent_events_task.delay(str(persistent_agent.id)))
                    
                    # Clear session data
                    if 'agent_charter' in request.session:
                        del request.session['agent_charter']
                    if 'agent_charter_source' in request.session:
                        del request.session['agent_charter_source']
                    if AIEmployeeTemplateService.TEMPLATE_SESSION_KEY in request.session:
                        del request.session[AIEmployeeTemplateService.TEMPLATE_SESSION_KEY]

                    transaction.on_commit(lambda:  Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_CREATED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(persistent_agent.id),
                            'agent_name': agent_name,
                            'contact_email': user_contact_email if user_contact_email else '',
                            'contact_sms': user_contact_sms if user_contact_sms else '',
                            'initial_message': initial_user_message,
                            'charter': initial_user_message if initial_user_message else '',
                            'preferred_contact_method': preferred_contact_method,
                            'template_code': selected_template.code if selected_template else '',
                            'template_schedule_applied': applied_schedule or '',
                        }
                    ))

                    return redirect('agent_welcome', pk=persistent_agent.id)
                    
            except Exception as e:
                messages.error(
                    request,
                    f"Error creating persistent agent: {str(e)}"
                )
        
        # If form is invalid or has errors, re-render with them
        context = self.get_context_data(form=form)
        context['form'] = form
        return self.render_to_response(context)

    @tracer.start_as_current_span("CONSOLE Agent Create Contact - Generate Unique Name")
    def _generate_unique_agent_name(self, user, max_attempts=10):
        """Generate a unique agent name for the given user."""
        for attempt in range(max_attempts):
            name = AgentNameGenerator.generate()
            
            # Check if this name is already taken by this user's agents
            if not BrowserUseAgent.objects.filter(user=user, name=name).exists():
                return name
                
        # Fallback: append a number to ensure uniqueness
        base_name = AgentNameGenerator.generate()
        counter = 1
        while BrowserUseAgent.objects.filter(user=user, name=f"{base_name} {counter}").exists():
            counter += 1
            if counter > 100:  # Safety valve
                raise ValueError("Unable to generate unique agent name after extensive attempts")
        
        return f"{base_name} {counter}"

    @tracer.start_as_current_span("CONSOLE Agent Create Contact - Generate Unique Email")
    def _generate_unique_agent_email(self, agent_name: str, max_attempts=100) -> str:
        """
        Generate a unique, user-friendly email address from the agent's name.
        e.g., "Atlas Core" -> "atlas.core@my.gobii.ai"
        """
        import re
        from django.utils.crypto import get_random_string

        # Sanitize the agent name into a username format
        base_username = agent_name.lower().strip()
        base_username = re.sub(r'\s+', '.', base_username)  # Replace spaces with dots
        base_username = re.sub(r'[^\w.]', '', base_username)  # Remove non-alphanumeric chars except dots
        from django.conf import settings as dj_settings
        domain = getattr(dj_settings, 'DEFAULT_AGENT_EMAIL_DOMAIN', 'my.gobii.ai')

        # First attempt
        email_address = f"{base_username}@{domain}"
        if not PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.EMAIL, address__iexact=email_address
        ).exists():
            return email_address

        # If it exists, append a number
        for i in range(2, max_attempts):
            email_address = f"{base_username}{i}@{domain}"
            if not PersistentAgentCommsEndpoint.objects.filter(
                channel=CommsChannel.EMAIL, address__iexact=email_address
            ).exists():
                return email_address
        
        # Final fallback with random string
        random_suffix = get_random_string(4)
        email_address = f"{base_username}-{random_suffix}@{domain}"
        if not PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.EMAIL, address__iexact=email_address
        ).exists():
            return email_address

        raise ValueError("Unable to generate a unique email address for the agent.")


class AgentEnableSmsView(LoginRequiredMixin, PhoneNumberMixin, TemplateView):
    """Enable SMS communication for an existing agent."""

    template_name = "console/agent_enable_sms.html"

    def dispatch(self, request, *args, **kwargs):
        self.agent = get_object_or_404(PersistentAgent, pk=kwargs["pk"], user=request.user)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        phone = self._current_phone()
        if SKIP_VERIFIED_SMS_SCREEN and phone and phone.is_verified:
            return self._enable_sms_and_redirect(phone)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        resp = self._handle_phone_post()
        if resp:
            return resp

        if "enable_sms" in request.POST:
            phone = self._current_phone()
            if not phone or not phone.is_verified:
                messages.error(request, "Please verify a phone number before enabling SMS.")
                return redirect(request.path)
            return self._enable_sms_and_redirect(phone)

        return super().get(request, *args, **kwargs)

    def _enable_sms_and_redirect(self, phone: UserPhoneNumber):
        try:
            with transaction.atomic():
                agent_sms = find_unused_number()

                agent_ep = PersistentAgentCommsEndpoint.objects.create(
                    owner_agent=self.agent,
                    channel=CommsChannel.SMS,
                    address=agent_sms.phone_number,
                    is_primary=True,
                )
                PersistentAgentSmsEndpoint.objects.create(
                    endpoint=agent_ep,
                    supports_mms=True,
                    carrier_name=agent_sms.provider,
                )

                user_ep, _ = PersistentAgentCommsEndpoint.objects.get_or_create(
                    channel=CommsChannel.SMS,
                    address__iexact=phone.phone_number,
                    defaults={"address": phone.phone_number, "owner_agent": None},
                )

                self.agent.preferred_contact_endpoint = user_ep
                self.agent.save(update_fields=["preferred_contact_endpoint"])

                try:
                    sms.send_sms(
                        to_number=phone.phone_number,
                        from_number=agent_sms.phone_number,
                        body="Gobii: You’ve enabled SMS communication with Gobii. Reply HELP for help, STOP to opt-out.",
                    )

                except Exception as e:
                    logger.error(
                        "Error sending initial SMS to user after enabling agent SMS: %s", str(e)
                    )

                conversation = _get_or_create_conversation(
                    channel=CommsChannel.SMS.value,
                    address=phone.phone_number,
                    owner_agent=self.agent,
                )
                _ensure_participant(
                    conversation,
                    user_ep,
                    PersistentAgentConversationParticipant.ParticipantRole.EXTERNAL,
                )
                _ensure_participant(
                    conversation,
                    agent_ep,
                    PersistentAgentConversationParticipant.ParticipantRole.AGENT,
                )

                PersistentAgentMessage.objects.create(
                    is_outbound=False,
                    from_endpoint=user_ep,
                    to_endpoint=agent_ep,
                    conversation=conversation,
                    body="Hi I've enabled SMS communication with you! Could you introduce yourself and confirm SMS is working?",
                    owner_agent=self.agent,
                )

                # Trigger the first event processing run after commit
                transaction.on_commit(lambda: process_agent_events_task.delay(str(self.agent.id)))

        except Exception as e:
            messages.error(
                self.request,
                f"Error enabling SMS: {str(e)}",
            )
            return redirect("agent_detail", pk=self.agent.pk)

        messages.success(self.request, "SMS has been enabled for this agent.")
        return redirect("agent_detail", pk=self.agent.pk)

class AgentDetailView(ConsoleViewMixin, DetailView):
    """Configuration page for a single agent.

    Uses ConsoleViewMixin to respect the current console context. When in
    organization context, only agents belonging to that organization are
    visible. In personal context, only the user's personal agents (no org)
    are visible.
    """
    model = PersistentAgent
    template_name = "console/agent_detail.html"
    context_object_name = "agent"
    pk_url_kwarg = "pk"

    @tracer.start_as_current_span("CONSOLE Agent Detail View - get_object")
    def get_queryset(self):
        """Scope agents to the active console context.

        - Organization context: agents owned by the org, and only if the user
          is an active member of that organization.
        - Personal context: user-owned agents without an organization.
        """
        qs = super().get_queryset()

        context_type = self.request.session.get('context_type', 'personal')
        if context_type == 'organization':
            org_id = self.request.session.get('context_id')
            # Verify membership; if not a member, return no rows to force 404
            if not OrganizationMembership.objects.filter(
                user=self.request.user,
                org_id=org_id,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).exists():
                return qs.none()

            return qs.filter(organization_id=org_id)

        # Personal context
        return qs.filter(user=self.request.user, organization__isnull=True)

    @tracer.start_as_current_span("CONSOLE Agent Detail View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add the primary email to the context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        
        # Find the primary email endpoint for this agent
        primary_email = agent.comms_endpoints.filter(
            channel=CommsChannel.EMAIL, is_primary=True
        ).first()

        primary_sms = agent.comms_endpoints.filter(
            channel=CommsChannel.SMS, is_primary=True
        ).first()

        context['primary_email'] = primary_email
        context['primary_sms'] = primary_sms
        
        # Always include allowlist configuration (flag removed)
        from api.models import CommsAllowlistEntry
        context['show_allowlist'] = True
        context['whitelist_policy'] = agent.whitelist_policy
        context['allowlist_entries'] = CommsAllowlistEntry.objects.filter(
            agent=agent
        ).order_by('channel', 'address')
        context['pending_invites'] = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).order_by('channel', 'address')

        # Count active allowlist entries AND pending invitations for display
        active_count = CommsAllowlistEntry.objects.filter(
            agent=agent,
            is_active=True
        ).count()
        pending_count = AgentAllowlistInvite.objects.filter(
            agent=agent,
            status=AgentAllowlistInvite.InviteStatus.PENDING
        ).count()
        context['active_allowlist_count'] = active_count + pending_count

        # Add pending contact requests count
        from api.models import CommsAllowlistRequest
        pending_contact_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).count()
        context['pending_contact_requests'] = pending_contact_requests

        # Add owner information for display
        context['owner_email'] = agent.user.email

        # Check if owner has verified phone for SMS display
        try:
            from api.models import UserPhoneNumber
            owner_phone = UserPhoneNumber.objects.filter(
                user=agent.user, 
                is_verified=True
            ).first()
            context['owner_phone'] = owner_phone.phone_number if owner_phone else None
        except:
            context['owner_phone'] = None

        return context

    @tracer.start_as_current_span("CONSOLE Agent Detail View - Post")
    def post(self, request, *args, **kwargs):
        """Handle agent configuration updates and allowlist management."""
        agent = self.get_object()
        
        # Handle AJAX allowlist operations
        # Check both modern header and legacy header for AJAX detection
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'
        
        if is_ajax:
            from django.http import JsonResponse
            from django.template.loader import render_to_string
            from api.models import CommsAllowlistEntry
            from django.core.exceptions import ValidationError
            from django.db import IntegrityError
            
            action = request.POST.get('action')
            
            if action == 'add_allowlist':
                channel = request.POST.get('channel', 'email')
                address = request.POST.get('address', '').strip()
                
                if not address:
                    return JsonResponse({'success': False, 'error': 'Address is required'})
                
                try:
                    # Check if they're already in the allowlist
                    existing_entry = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        channel=channel,
                        address=address
                    ).first()
                    
                    if existing_entry:
                        if existing_entry.is_active:
                            return JsonResponse({'success': False, 'error': 'This address is already in the allowlist'})
                        else:
                            # Reactivate the existing entry and update inbound/outbound settings
                            existing_entry.is_active = True
                            # Update inbound/outbound settings from POST or keep existing
                            allow_inbound = request.POST.get('allow_inbound')
                            allow_outbound = request.POST.get('allow_outbound')
                            if allow_inbound is not None:
                                existing_entry.allow_inbound = allow_inbound.lower() == 'true'
                            if allow_outbound is not None:
                                existing_entry.allow_outbound = allow_outbound.lower() == 'true'
                            existing_entry.save(update_fields=['is_active', 'allow_inbound', 'allow_outbound'])
                            entry = existing_entry
                    else:
                        # Directly create the allowlist entry (skip invitation process)
                        # Get inbound/outbound settings from POST or default to both
                        allow_inbound = request.POST.get('allow_inbound', 'true').lower() == 'true'
                        allow_outbound = request.POST.get('allow_outbound', 'true').lower() == 'true'
                        
                        entry = CommsAllowlistEntry.objects.create(
                            agent=agent,
                            channel=channel,
                            address=address,
                            is_active=True,
                            allow_inbound=allow_inbound,
                            allow_outbound=allow_outbound
                        )

                        Analytics.track_event(
                            user_id=request.user.id,
                            event=AnalyticsEvent.AGENT_CONTACTS_APPROVED,
                            source=AnalyticsSource.WEB,
                            properties={
                                'agent_id': str(agent.id),
                                'channel': channel,
                                'address': address,
                            }
                        )

                    from api.agent.tasks.process_events import process_agent_events_task
                    process_agent_events_task.delay(str(agent.id))
                    
                    # Switch agent to manual allowlist mode if not already
                    # (though it should already be manual with our new changes)
                    if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                        agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                        agent.save(update_fields=['whitelist_policy'])
                    
                    # Render updated list
                    entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                    
                    # We no longer create pending invites from the agent config page
                    # but there might be some from other flows, so we still check
                    pending_invites = AgentAllowlistInvite.objects.filter(
                        agent=agent, 
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).order_by('channel', 'address')
                    
                    # Add owner information for display
                    owner_email = agent.user.email
                    owner_phone = None
                    try:
                        from api.models import UserPhoneNumber
                        phone_obj = UserPhoneNumber.objects.filter(
                            user=agent.user, 
                            is_verified=True
                        ).first()
                        owner_phone = phone_obj.phone_number if phone_obj else None
                    except:
                        pass
                    
                    html = render_to_string('console/partials/_allowlist_entries_inline.html', {
                        'allowlist_entries': entries,
                        'pending_invites': pending_invites,
                        'owner_email': owner_email,
                        'owner_phone': owner_phone,
                    })
                    
                    # Count active entries for the counter
                    active_count = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True
                    ).count()
                    
                    # Also count any remaining pending invitations from other flows
                    pending_count = AgentAllowlistInvite.objects.filter(
                        agent=agent,
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).count()
                    total_count = active_count + pending_count
                    
                    return JsonResponse({'success': True, 'html': html, 'active_count': total_count})
                    
                except ValidationError as e:
                    # Handle ValidationError properly
                    error_msg = 'Validation error'
                    if hasattr(e, 'message_dict'):
                        # Get first error message from the dict
                        for field, msgs in e.message_dict.items():
                            if msgs:
                                error_msg = msgs[0] if isinstance(msgs[0], str) else str(msgs[0])
                                break
                    elif hasattr(e, 'messages') and e.messages:
                        error_msg = e.messages[0] if isinstance(e.messages[0], str) else str(e.messages[0])
                    else:
                        error_msg = str(e)
                    return JsonResponse({'success': False, 'error': error_msg})
                except IntegrityError:
                    return JsonResponse({'success': False, 'error': 'This address is already in the allowlist'})
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)})
            
            elif action == 'remove_allowlist':
                entry_id = request.POST.get('entry_id')
                
                try:
                    CommsAllowlistEntry.objects.filter(agent=agent, id=entry_id).delete()
                    
                    # Render updated list
                    entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                    pending_invites = AgentAllowlistInvite.objects.filter(
                        agent=agent, 
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).order_by('channel', 'address')
                    
                    # Add owner information for display
                    owner_email = agent.user.email
                    owner_phone = None
                    try:
                        from api.models import UserPhoneNumber
                        phone_obj = UserPhoneNumber.objects.filter(
                            user=agent.user, 
                            is_verified=True
                        ).first()
                        owner_phone = phone_obj.phone_number if phone_obj else None
                    except:
                        pass
                    
                    html = render_to_string('console/partials/_allowlist_entries_inline.html', {
                        'allowlist_entries': entries,
                        'pending_invites': pending_invites,
                        'owner_email': owner_email,
                        'owner_phone': owner_phone,
                    })
                    
                    # Count active entries AND pending invitations for the counter
                    active_count = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True
                    ).count()
                    pending_count = AgentAllowlistInvite.objects.filter(
                        agent=agent,
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).count()
                    total_count = active_count + pending_count
                    
                    return JsonResponse({'success': True, 'html': html, 'active_count': total_count})
                    
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)})
            
            elif action == 'cancel_invite':
                invite_id = request.POST.get('invite_id')
                
                try:
                    # Find and delete the invitation
                    AgentAllowlistInvite.objects.filter(agent=agent, id=invite_id).delete()
                    
                    # Render updated list
                    entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                    pending_invites = AgentAllowlistInvite.objects.filter(
                        agent=agent, 
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).order_by('channel', 'address')
                    
                    # Add owner information for display
                    owner_email = agent.user.email
                    owner_phone = None
                    try:
                        from api.models import UserPhoneNumber
                        phone_obj = UserPhoneNumber.objects.filter(
                            user=agent.user, 
                            is_verified=True
                        ).first()
                        owner_phone = phone_obj.phone_number if phone_obj else None
                    except:
                        pass
                    
                    html = render_to_string('console/partials/_allowlist_entries_inline.html', {
                        'allowlist_entries': entries,
                        'pending_invites': pending_invites,
                        'owner_email': owner_email,
                        'owner_phone': owner_phone,
                    })
                    
                    # Count active entries AND pending invitations for the counter
                    active_count = CommsAllowlistEntry.objects.filter(
                        agent=agent,
                        is_active=True
                    ).count()
                    pending_count = AgentAllowlistInvite.objects.filter(
                        agent=agent,
                        status=AgentAllowlistInvite.InviteStatus.PENDING
                    ).count()
                    total_count = active_count + pending_count
                    
                    return JsonResponse({'success': True, 'html': html, 'active_count': total_count})
                    
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)})
            
            return JsonResponse({'success': False, 'error': 'Invalid action'})
        
        # Handle regular form submission
        # Check if this is an allowlist action that shouldn't have gotten here
        action = request.POST.get('action', '')
        if action in ['add_allowlist', 'remove_allowlist']:
            # This shouldn't happen, but if JavaScript failed, redirect back
            # Import messages here if needed
            from django.contrib import messages as django_messages
            django_messages.error(request, "Please enable JavaScript to manage the allowlist.")
            return redirect('agent_detail', pk=agent.pk)
        
        new_name = request.POST.get('name', '').strip()
        new_charter = request.POST.get('charter', '').strip()
        # Checkbox inputs are only present in POST data when checked. Determine the desired
        # active state based on whether the "is_active" field was submitted.
        new_is_active = 'is_active' in request.POST
        
        # Handle whitelist policy update (flag removed)
        new_whitelist_policy = request.POST.get('whitelist_policy', '').strip()

        if not new_name:
            messages.error(request, "Agent name cannot be empty.")
            return redirect('agent_detail', pk=agent.pk)

        if not new_charter:
            messages.error(request, "Agent assignment cannot be empty.")
            return redirect('agent_detail', pk=agent.pk)

        # Check for uniqueness, excluding the current agent's BrowserUseAgent
        if BrowserUseAgent.objects.filter(
            user=request.user, 
            name=new_name
        ).exclude(pk=agent.browser_use_agent.pk).exists():
            messages.error(request, f"You already have an agent named '{new_name}'.")
            return redirect('agent_detail', pk=agent.pk)

        try:
            with transaction.atomic():
                # Track which fields changed
                agent_fields_to_update = []
                browser_agent_fields_to_update = []

                # Update names if they changed
                if agent.name != new_name:
                    agent.name = new_name
                    agent.browser_use_agent.name = new_name
                    agent_fields_to_update.append('name')
                    browser_agent_fields_to_update.append('name')

                # Update charter if it changed
                if agent.charter != new_charter:
                    agent.charter = new_charter
                    agent_fields_to_update.append('charter')

                # Update active status if it changed
                if agent.is_active != new_is_active:
                    agent.is_active = new_is_active
                    agent_fields_to_update.append('is_active')
                
                # Update whitelist policy if provided and changed
                if new_whitelist_policy and agent.whitelist_policy != new_whitelist_policy:
                    if new_whitelist_policy in [choice[0] for choice in PersistentAgent.WhitelistPolicy.choices]:
                        agent.whitelist_policy = new_whitelist_policy
                        agent_fields_to_update.append('whitelist_policy')

                # Mark interaction time and reactivate if previously expired
                from django.utils import timezone
                agent.last_interaction_at = timezone.now()
                agent_fields_to_update.append('last_interaction_at')

                # Persist changes if needed
                if agent_fields_to_update:
                    agent.save(update_fields=agent_fields_to_update)
                if browser_agent_fields_to_update:
                    agent.browser_use_agent.save(update_fields=browser_agent_fields_to_update)

                # If agent was soft-expired, restore schedule (from snapshot if missing) and mark active
                if agent.life_state == PersistentAgent.LifeState.EXPIRED and agent.is_active:
                    fields = []
                    if agent.schedule_snapshot:
                        agent.schedule = agent.schedule_snapshot
                        fields.append('schedule')
                    agent.life_state = PersistentAgent.LifeState.ACTIVE
                    fields.append('life_state')
                    agent.save(update_fields=fields)

                messages.success(request, "Agent updated successfully.")

                Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_UPDATED,
                    source=AnalyticsSource.WEB,
                    properties={
                        'agent_id': str(agent.pk),
                        'agent_name': new_name,
                        'is_active': new_is_active,
                        'charter': new_charter,
                    }
                )
        except Exception as e:
            messages.error(request, f"Error updating agent: {e}")

        return redirect('agent_detail', pk=agent.pk)


class AgentAllowlistView(LoginRequiredMixin, TemplateView):
    """Manage manual allowlist and policy for an agent."""
    template_name = "console/agent_allowlist.html"

    def _get_agent(self):
        pk = self.kwargs.get('pk')
        agent = PersistentAgent.objects.filter(pk=pk).select_related('organization').first()
        if not agent:
            raise Http404
        if not self._can_manage(self.request.user, agent):
            raise PermissionDenied
        return agent

    def _can_manage(self, user, agent: PersistentAgent) -> bool:
        if agent.user_id == user.id:
            return True
        if agent.organization_id:
            return OrganizationMembership.objects.filter(
                org=agent.organization,
                user=user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
                role__in=[OrganizationMembership.OrgRole.OWNER, OrganizationMembership.OrgRole.ADMIN],
            ).exists()
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self._get_agent()
        context['agent'] = agent
        context['entries'] = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
        context['form'] = kwargs.get('form') or AllowlistEntryForm()
        context['policy'] = agent.whitelist_policy
        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        agent = self._get_agent()
        action = request.POST.get('action')

        if action == 'add':
            form = AllowlistEntryForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Please correct the errors below.")
                if request.headers.get('HX-Request'):
                    # Return entries list unchanged
                    ctx = self.get_context_data(form=form)
                    return render(request, 'console/partials/_allowlist_entries.html', { 'entries': ctx['entries'] })
                return render(request, self.template_name, self.get_context_data(form=form))
            try:
                from django.db import IntegrityError

                entry = CommsAllowlistEntry(
                    agent=agent,
                    channel=form.cleaned_data['channel'],
                    address=form.cleaned_data['address'],
                    allow_inbound=form.cleaned_data.get('allow_inbound', True),
                    allow_outbound=form.cleaned_data.get('allow_outbound', True),
                )
                entry.full_clean()  # This will run model validation
                entry.save()

                messages.success(request, "Allowlist entry added.")
            except (ValidationError, IntegrityError) as e:
                messages.error(request, f"Could not add entry: {e}")
            if request.headers.get('HX-Request'):
                entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                return render(request, 'console/partials/_allowlist_entries.html', { 'entries': entries })

        elif action == 'delete':
            entry_id = request.POST.get('entry_id')
            deleted = CommsAllowlistEntry.objects.filter(agent=agent, id=entry_id).delete()[0]
            if deleted:
                messages.success(request, "Allowlist entry deleted.")
            else:
                messages.error(request, "Entry not found.")
            if request.headers.get('HX-Request'):
                entries = CommsAllowlistEntry.objects.filter(agent=agent).order_by('channel', 'address')
                return render(request, 'console/partials/_allowlist_entries.html', { 'entries': entries })

        elif action == 'policy':
            policy = request.POST.get('whitelist_policy')
            if policy in dict(PersistentAgent.WhitelistPolicy.choices):
                agent.whitelist_policy = policy
                agent.save(update_fields=['whitelist_policy'])
                messages.success(request, "Whitelist policy updated.")
            else:
                messages.error(request, "Invalid policy value.")

        return redirect('agent_allowlist', pk=agent.pk)

class AgentDeleteView(LoginRequiredMixin, View):
    """Handle agent deletion."""

    @transaction.atomic
    @tracer.start_as_current_span("CONSOLE Agent Delete View - delete")
    def delete(self, request, *args, **kwargs):
        try:
            agent = PersistentAgent.objects.get(
                pk=self.kwargs['pk'],
                user=request.user
            )
            
            agent_name = agent.name
            # Store the browser_use_agent reference before deleting the persistent agent
            browser_use_agent = agent.browser_use_agent
            
            # Delete the persistent agent first (this removes the foreign key constraint)
            agent.delete()
            
            # Now delete the browser use agent
            browser_use_agent.delete()
            
            messages.success(request, f"Agent '{agent_name}' has been deleted.")

            transaction.on_commit(lambda: Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.PERSISTENT_AGENT_DELETED,
                source=AnalyticsSource.WEB,
                properties={
                    'agent_id': str(agent.pk),
                    'agent_name': agent_name,
                }
            ))

            response = HttpResponse(status=200)
            response['HX-Redirect'] = reverse('agents')
            return response
            
        except PersistentAgent.DoesNotExist:
            return HttpResponse("Agent not found or you don't have permission.", status=404)
        except Exception as e:
            return HttpResponse(f"An error occurred: {e}", status=500)

class AgentSecretsView(LoginRequiredMixin, TemplateView):
    """Secrets management page for a single agent."""
    template_name = "console/agent_secrets.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and secrets to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent
        
        # Get secrets from the new model, split by requested/fulfilled
        from api.models import PersistentAgentSecret
        fulfilled_qs = PersistentAgentSecret.objects.filter(agent=agent, requested=False).order_by('domain_pattern', 'name')
        requested_qs = PersistentAgentSecret.objects.filter(agent=agent, requested=True).order_by('domain_pattern', 'name')

        # Group fulfilled secrets by domain for display
        secrets = {}
        for secret in fulfilled_qs:
            if secret.domain_pattern not in secrets:
                secrets[secret.domain_pattern] = {}
            secrets[secret.domain_pattern][secret.name] = {
                'id': secret.id,
                'name': secret.name,
                'description': secret.description,
                'key': secret.key,
                'created_at': secret.created_at,
                'updated_at': secret.updated_at
            }
        context['secrets'] = secrets
        context['has_secrets'] = bool(secrets)
        context['requested_secrets'] = requested_qs
        context['has_requested_secrets'] = requested_qs.exists()

        return context


class AgentSecretsAddView(LoginRequiredMixin, View):
    """Add a new secret to an agent."""

    @tracer.start_as_current_span("CONSOLE Agent Secrets Add")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    def post(self, request, *args, **kwargs):
        """Handle adding a new secret."""
        agent = self.get_object()
        form = PersistentAgentAddSecretForm(request.POST, agent=agent)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    from api.models import PersistentAgentSecret
                    
                    # Create the new secret
                    domain = form.cleaned_data['domain']
                    name = form.cleaned_data['name']
                    description = form.cleaned_data.get('description', '')
                    value = form.cleaned_data['value']
                    
                    # Create and save the secret
                    secret = PersistentAgentSecret(
                        agent=agent,
                        domain_pattern=domain,
                        name=name,
                        description=description
                    )
                    # The key will be auto-generated in the clean() method
                    secret.full_clean()  # This generates the key from name
                    secret.set_value(value)  # This validates and encrypts the value
                    secret.save()
                    
                    messages.success(request, f"Secret '{name}' added successfully for domain '{domain}'.")

                    # Count total secrets for analytics
                    total_secrets = PersistentAgentSecret.objects.filter(agent=agent).count()

                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(agent.pk),
                            'agent_name': agent.name,
                            'secret_name': name,
                            'secret_key': secret.key,  # Generated key
                            'domain': domain,
                            'total_secrets': total_secrets,
                        }
                    ))

            except Exception as e:
                logger.error(f"Failed to add secret to agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to add secret. Please try again.")
        
        # Handle form errors by showing them as messages
        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        
        return redirect('agent_secrets', pk=agent.pk)


class AgentSecretsEditView(LoginRequiredMixin, TemplateView):
    """Edit view for existing secret value (GET render + POST update)."""
    template_name = "console/agent_secret_edit.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Edit - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    def get(self, request, *args, **kwargs):
        """Load secret by ID for edit form."""
        agent = self.get_object()
        secret_id = kwargs.get('secret_id') or self.kwargs.get('secret_id')

        from api.models import PersistentAgentSecret

        if not secret_id:
            messages.error(request, "Secret ID is required.")
            return redirect('agent_secrets', pk=agent.pk)

        try:
            secret = PersistentAgentSecret.objects.get(agent=agent, pk=secret_id)
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
            return redirect('agent_secrets', pk=agent.pk)

        # Store the secret in kwargs for other methods
        kwargs['secret_obj'] = secret
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Secrets Edit - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent, secret info, and form to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        secret_obj = kwargs.get('secret_obj')
        
        context['agent'] = agent
        context['secret_key'] = secret_obj.key if secret_obj else None
        context['secret_name'] = secret_obj.name if secret_obj else None
        context['domain'] = secret_obj.domain_pattern if secret_obj else None
        context['form'] = PersistentAgentEditSecretForm(agent=agent, secret=secret_obj)
        return context

    def post(self, request, *args, **kwargs):
        """Handle form submission for editing a secret value."""
        agent = self.get_object()
        secret_id = kwargs.get('secret_id') or self.kwargs.get('secret_id')

        if not secret_id:
            messages.error(request, "Secret ID is required.")
            return redirect('agent_secrets', pk=agent.pk)

        # Find the secret by ID
        from api.models import PersistentAgentSecret
        try:
            secret = PersistentAgentSecret.objects.get(agent=agent, pk=secret_id)
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
            return redirect('agent_secrets', pk=agent.pk)

        form = PersistentAgentEditSecretForm(request.POST, agent=agent, secret=secret)

        if form.is_valid():
            try:
                with transaction.atomic():
                    # Update the secret fields
                    new_name = form.cleaned_data['name']
                    new_description = form.cleaned_data.get('description', '')
                    new_value = form.cleaned_data['value']
                    
                    # Update name and description
                    secret.name = new_name
                    secret.description = new_description
                    secret.full_clean()  # This will regenerate the key if name changed
                    secret.set_value(new_value)  # This validates and encrypts the value
                    secret.save()
                    
                    messages.success(request, f"Secret '{secret.name}' updated successfully.")

                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_UPDATED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(agent.pk),
                            'agent_name': agent.name,
                            'secret_name': secret.name,
                            'secret_key': secret.key,
                            'domain': secret.domain_pattern,
                        }
                    ))

                    return redirect('agent_secrets', pk=agent.pk)

            except Exception as e:
                logger.error(f"Failed to edit secret for agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to update secret. Please try again.")
        
        # If form is invalid or exception occurred, re-render with errors
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)


class AgentSecretsDeleteView(LoginRequiredMixin, View):
    """Delete a secret from an agent."""

    @tracer.start_as_current_span("CONSOLE Agent Secrets Delete")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    def post(self, request, *args, **kwargs):
        """Handle deleting a secret by secret ID."""
        agent = self.get_object()
        secret_id = kwargs.get('secret_id')

        if not secret_id:
            messages.error(request, "Secret ID is required.")
            return redirect('agent_secrets', pk=agent.pk)

        # Get the specific secret by ID
        try:
            from api.models import PersistentAgentSecret
            secret = PersistentAgentSecret.objects.get(
                pk=secret_id,
                agent=agent
            )
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
            return redirect('agent_secrets', pk=agent.pk)

        try:
            with transaction.atomic():
                secret_key = secret.key
                secret_domain = secret.domain_pattern

                # Delete the secret
                secret.delete()
                
                messages.success(request, f"Secret '{secret_key}' deleted successfully.")

                # Count remaining secrets for analytics
                remaining_secrets = PersistentAgentSecret.objects.filter(agent=agent).count()

                transaction.on_commit(lambda: Analytics.track_event(
                    user_id=request.user.id,
                    event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_DELETED,
                    source=AnalyticsSource.WEB,
                    properties={
                        'agent_id': str(agent.pk),
                        'agent_name': agent.name,
                        'secret_key': secret_key,
                        'domain': secret_domain,
                        'remaining_secrets': remaining_secrets,
                    }
                ))

        except Exception as e:
            logger.error(f"Failed to delete secret {secret_id} for agent {agent.id}: {str(e)}")
            messages.error(request, "Failed to delete secret. Please try again.")
        
        return redirect('agent_secrets', pk=agent.pk)


class AgentEmailSettingsView(LoginRequiredMixin, TemplateView):
    """Simple console page to edit an agent-owned email account settings."""
    template_name = "console/agent_email_settings.html"

    def get_agent(self):
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user,
        )

    def _get_email_endpoint(self, agent: PersistentAgent):
        ep = agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, owner_agent=agent, is_primary=True).first()
        if not ep:
            ep = agent.comms_endpoints.filter(channel=CommsChannel.EMAIL, owner_agent=agent).first()
        return ep

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self.get_agent()
        endpoint = self._get_email_endpoint(agent)
        account = getattr(endpoint, 'agentemailaccount', None) if endpoint else None
        from django.conf import settings as dj_settings
        default_domain = getattr(dj_settings, 'DEFAULT_AGENT_EMAIL_DOMAIN', 'my.gobii.ai')
        is_default_endpoint = False
        if endpoint and endpoint.address and default_domain:
            try:
                is_default_endpoint = endpoint.address.lower().endswith('@' + default_domain.lower())
            except Exception:
                is_default_endpoint = False

        initial = {}
        if account:
            initial = {
                'smtp_host': account.smtp_host,
                'smtp_port': account.smtp_port,
                'smtp_security': account.smtp_security,
                'smtp_auth': account.smtp_auth,
                'smtp_username': account.smtp_username,
                'is_outbound_enabled': account.is_outbound_enabled,
                'imap_host': account.imap_host,
                'imap_port': account.imap_port,
                'imap_security': account.imap_security,
                'imap_username': account.imap_username,
                'imap_folder': account.imap_folder,
                'is_inbound_enabled': account.is_inbound_enabled,
                'imap_idle_enabled': account.imap_idle_enabled,
                'poll_interval_sec': account.poll_interval_sec,
            }

        context['agent'] = agent
        context['endpoint'] = endpoint
        context['account'] = account
        context['is_default_endpoint'] = is_default_endpoint
        context['default_domain'] = default_domain
        context['form'] = AgentEmailAccountConsoleForm(initial=initial)
        return context

    def post(self, request, *args, **kwargs):
        agent = self.get_agent()
        endpoint = self._get_email_endpoint(agent)
        if not endpoint:
            # Allow creating endpoint directly from this page
            action = request.POST.get('action')
            if action == 'create_endpoint':
                address = (request.POST.get('address') or '').strip()
                if not address or '@' not in address:
                    messages.error(request, "Please provide a valid email address (e.g., agent@example.com).")
                    return redirect('agent_email_settings', pk=agent.pk)
                from api.models import PersistentAgentCommsEndpoint, CommsChannel
                try:
                    ep = PersistentAgentCommsEndpoint.objects.create(
                        owner_agent=agent,
                        channel=CommsChannel.EMAIL,
                        address=address,
                        is_primary=True,
                    )
                    messages.success(request, "Agent email endpoint created.")
                    return redirect('agent_email_settings', pk=agent.pk)
                except Exception as e:
                    messages.error(request, f"Failed to create email endpoint: {e}")
                    return redirect('agent_email_settings', pk=agent.pk)
            else:
                messages.error(request, "This agent has no email endpoint yet. Provide an email address to create one.")
                return redirect('agent_email_settings', pk=agent.pk)

        form = AgentEmailAccountConsoleForm(request.POST)
        action = request.POST.get('action', 'save')
        if not form.is_valid() and action == 'save':
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
            return redirect('agent_email_settings', pk=agent.pk)

        # Load or create account for save/test operations
        from api.models import AgentEmailAccount
        account = getattr(endpoint, 'agentemailaccount', None)

        # Handle save
        if action == 'save':
            data = form.cleaned_data
            created = False
            if not account:
                account = AgentEmailAccount(endpoint=endpoint)
                created = True
            # Update endpoint address to match user-entered value, if provided
            new_address = (request.POST.get('endpoint_address') or '').strip()
            if new_address and new_address != endpoint.address:
                try:
                    endpoint.address = new_address
                    endpoint.save(update_fields=['address'])
                except Exception as e:
                    messages.error(request, f"Failed to update agent email address: {e}")
                    return redirect('agent_email_settings', pk=agent.pk)
            # Assign simple fields
            for f in ('smtp_host', 'smtp_port', 'smtp_security', 'smtp_auth', 'smtp_username', 'is_outbound_enabled',
                      'imap_host', 'imap_port', 'imap_security', 'imap_username', 'imap_folder', 'is_inbound_enabled', 'imap_idle_enabled',
                      'poll_interval_sec'):
                setattr(account, f, data.get(f))
            # Passwords
            from api.encryption import SecretsEncryption
            if data.get('smtp_password'):
                account.smtp_password_encrypted = SecretsEncryption.encrypt_value(data.get('smtp_password'))
            if data.get('imap_password'):
                account.imap_password_encrypted = SecretsEncryption.encrypt_value(data.get('imap_password'))
            try:
                account.full_clean()
                account.save()
                messages.success(request, "Email settings saved.")
                # Analytics for create/update
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.EMAIL_ACCOUNT_CREATED if created else AnalyticsEvent.EMAIL_ACCOUNT_UPDATED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address},
                    )
                except Exception:
                    pass
            except ValidationError as e:
                for field, errs in e.message_dict.items():
                    for err in errs:
                        messages.error(request, f"{field}: {err}")
            return redirect('agent_email_settings', pk=agent.pk)

        # Ensure account exists before tests / poll
        if not account:
            messages.error(request, "Please save email settings before testing or polling.")
            return redirect('agent_email_settings', pk=agent.pk)

        # Test SMTP
        if action == 'test_smtp':
            try:
                import smtplib
                if account.smtp_security == AgentEmailAccount.SmtpSecurity.SSL:
                    client = smtplib.SMTP_SSL(account.smtp_host, int(account.smtp_port or 465), timeout=30)
                else:
                    client = smtplib.SMTP(account.smtp_host, int(account.smtp_port or 587), timeout=30)
                try:
                    client.ehlo()
                    if account.smtp_security == AgentEmailAccount.SmtpSecurity.STARTTLS:
                        client.starttls()
                        client.ehlo()
                    if account.smtp_auth != AgentEmailAccount.AuthMode.NONE:
                        client.login(account.smtp_username or '', account.get_smtp_password() or '')
                    try:
                        client.noop()
                    except Exception:
                        pass
                finally:
                    try:
                        client.quit()
                    except Exception:
                        try:
                            client.close()
                        except Exception:
                            pass
                from django.utils import timezone
                account.connection_last_ok_at = timezone.now()
                account.connection_error = ""
                account.save(update_fields=['connection_last_ok_at', 'connection_error'])
                messages.success(request, "SMTP test succeeded.")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.SMTP_TEST_PASSED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address},
                    )
                except Exception:
                    pass
            except Exception as e:
                account.connection_error = str(e)
                account.save(update_fields=['connection_error'])
                messages.error(request, f"SMTP test failed: {e}")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.SMTP_TEST_FAILED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address, 'error': str(e)[:500]},
                    )
                except Exception:
                    pass
            return redirect('agent_email_settings', pk=agent.pk)

        # Test IMAP
        if action == 'test_imap':
            try:
                import imaplib
                if account.imap_security == AgentEmailAccount.ImapSecurity.SSL:
                    client = imaplib.IMAP4_SSL(account.imap_host, int(account.imap_port or 993), timeout=30)
                else:
                    client = imaplib.IMAP4(account.imap_host, int(account.imap_port or 143), timeout=30)
                    if account.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
                        client.starttls()
                try:
                    client.login(account.imap_username or '', account.get_imap_password() or '')
                    client.select(account.imap_folder or 'INBOX', readonly=True)
                    try:
                        client.noop()
                    except Exception:
                        pass
                finally:
                    try:
                        client.logout()
                    except Exception:
                        try:
                            client.shutdown()
                        except Exception:
                            pass
                from django.utils import timezone
                account.connection_last_ok_at = timezone.now()
                account.connection_error = ""
                account.save(update_fields=['connection_last_ok_at', 'connection_error'])
                messages.success(request, "IMAP test succeeded.")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.IMAP_TEST_PASSED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address},
                    )
                except Exception:
                    pass
            except Exception as e:
                account.connection_error = str(e)
                account.save(update_fields=['connection_error'])
                messages.error(request, f"IMAP test failed: {e}")
                try:
                    Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.IMAP_TEST_FAILED,
                        source=AnalyticsSource.WEB,
                        properties={'agent_id': str(agent.pk), 'endpoint': endpoint.address, 'error': str(e)[:500]},
                    )
                except Exception:
                    pass
            return redirect('agent_email_settings', pk=agent.pk)

        # Poll now
        if action == 'poll_now':
            try:
                from api.agent.tasks import poll_imap_inbox
                poll_imap_inbox.delay(str(account.pk))
                messages.success(request, "IMAP poll enqueued.")
            except Exception as e:
                messages.error(request, f"Failed to enqueue IMAP poll: {e}")
            return redirect('agent_email_settings', pk=agent.pk)

        # Default: redirect back
        return redirect('agent_email_settings', pk=agent.pk)


class AgentSecretsAddFormView(LoginRequiredMixin, TemplateView):
    """Form view for adding a new secret to an agent."""
    template_name = "console/agent_secret_add.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Add Form View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets Add Form View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and form to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent
        context['form'] = PersistentAgentAddSecretForm(agent=agent)
        return context

    def post(self, request, *args, **kwargs):
        """Handle form submission."""
        agent = self.get_object()
        form = PersistentAgentAddSecretForm(request.POST, agent=agent)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    from api.models import PersistentAgentSecret
                    
                    # Create the new secret
                    domain = form.cleaned_data['domain']
                    name = form.cleaned_data['name']
                    description = form.cleaned_data.get('description', '')
                    value = form.cleaned_data['value']

                    # Create and save the secret
                    secret = PersistentAgentSecret(
                        agent=agent,
                        domain_pattern=domain,
                        name=name,
                        description=description
                    )
                    # The key will be auto-generated in the clean() method
                    secret.full_clean()  # This generates the key from name
                    secret.set_value(value)  # This validates and encrypts the value
                    secret.save()

                    messages.success(request, f"Secret '{name}' added successfully for domain '{domain}'.")

                    # Count total secrets for analytics
                    total_secrets = PersistentAgentSecret.objects.filter(agent=agent).count()

                    transaction.on_commit(lambda: Analytics.track_event(
                        user_id=request.user.id,
                        event=AnalyticsEvent.PERSISTENT_AGENT_SECRET_ADDED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'agent_id': str(agent.pk),
                            'agent_name': agent.name,
                            'secret_name': name,
                            'secret_key': secret.key,  # Generated key
                            'domain': domain,
                            'total_secrets': total_secrets,
                        }
                    ))

                    return redirect('agent_secrets', pk=agent.pk)

            except Exception as e:
                logger.error(f"Failed to add secret to agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to add secret. Please try again.")
        
        # If form is invalid or exception occurred, re-render with errors
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)


# (Consolidated) AgentSecretsEditFormView removed; logic merged into AgentSecretsEditView

@login_required
@require_POST
@tracer.start_as_current_span("GRANT_CREDITS")
def grant_credits(request):
    """Endpoint to grant 100 task credits to a user. Admin only."""

    # Check if user is staff/admin
    if not request.user.is_staff:
        return JsonResponse({'success': False, 'error': 'Unauthorized. Admin access required.'}, status=403)

    user_id = request.POST.get('user_id')
    if not user_id:
        return JsonResponse({'success': False, 'error': 'User ID is required.'}, status=400)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)

    try:
        with transaction.atomic():
            # Create a new TaskCredit record for compensation
            grant_date = timezone.now()
            expiration_date = grant_date + timedelta(days=365)  # 1 year expiration for admin grants

            task_credit = TaskCredit.objects.create(
                user=user,
                credits=100,
                credits_used=0,
                granted_date=grant_date,
                expiration_date=expiration_date,
                plan=PlanNamesChoices.FREE,  # Use FREE plan for admin grants
                grant_type=GrantTypeChoices.COMPENSATION,
                additional_task=False,
                voided=False
            )

            logger.info(f"Admin {request.user.id} granted 100 task credits to user {user.id}")

            return JsonResponse({
                'success': True,
                'message': f"100 task credits granted to {user.email or user.username}.",
                'credits_granted': 100
            })

    except Exception as e:
        logger.error(f"Failed to grant credits to user {user_id}: {str(e)}")
        return JsonResponse({'success': False, 'error': f"Failed to grant credits: {str(e)}"}, status=500)


class AgentSecretsRequestView(LoginRequiredMixin, TemplateView):
    """View for displaying requested secrets that need values."""
    template_name = "console/agent_secrets_request.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and requested secrets to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent

        # Get requested secrets (those that have requested=True)
        from api.models import PersistentAgentSecret
        requested_secrets = PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True
        ).order_by('domain_pattern', 'name')

        context['requested_secrets'] = requested_secrets
        context['has_requested_secrets'] = requested_secrets.exists()
        context['form'] = PersistentAgentSecretsRequestForm(requested_secrets=requested_secrets)

        return context

    def post(self, request, *args, **kwargs):
        """Handle saving values or removing requested secrets."""
        agent = self.get_object()
        action = (request.POST.get('action') or '').strip().lower()

        from api.models import PersistentAgentSecret

        # Bulk remove requested secrets
        if request.resolver_match.url_name == 'agent_requested_secrets_remove' or action == 'remove_selected':
            try:
                ids = request.POST.getlist('secret_ids')
                if not ids:
                    messages.info(request, "No requests selected for removal.")
                    return redirect('agent_secrets_request', pk=agent.pk)
                with transaction.atomic():
                    qs = PersistentAgentSecret.objects.filter(agent=agent, requested=True, id__in=ids)
                    deleted_count = qs.count()
                    qs.delete()
                messages.success(request, f"Removed {deleted_count} requested credential(s).")
            except Exception as e:
                logger.error(f"Failed to bulk remove requested secrets for agent {agent.id}: {e}")
                messages.error(request, "Failed to remove selected requests.")
            return redirect('agent_secrets_request', pk=agent.pk)

        # Single remove via per-row action
        if request.resolver_match.url_name == 'agent_requested_secret_remove':
            secret_id = self.kwargs.get('secret_id')
            try:
                with transaction.atomic():
                    secret = PersistentAgentSecret.objects.get(agent=agent, id=secret_id, requested=True)
                    name = secret.name
                    secret.delete()
                messages.success(request, f"Removed request for '{name}'.")
            except PersistentAgentSecret.DoesNotExist:
                messages.error(request, "Requested secret not found.")
            except Exception as e:
                logger.error(f"Failed to remove requested secret {secret_id} for agent {agent.id}: {e}")
                messages.error(request, "Failed to remove request.")
            return redirect('agent_secrets_request', pk=agent.pk)

        # Default: save provided values (partial allowed)
        requested_secrets = PersistentAgentSecret.objects.filter(
            agent=agent,
            requested=True
        ).order_by('domain_pattern', 'name')

        form = PersistentAgentSecretsRequestForm(request.POST, requested_secrets=requested_secrets)

        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_count = 0
                    for secret in requested_secrets:
                        field_name = f'secret_{secret.id}'
                        value = form.cleaned_data.get(field_name)
                        if value:
                            secret.set_value(value)
                            secret.requested = False
                            secret.save()
                            updated_count += 1

                    if updated_count > 0:
                        from api.models import PersistentAgentStep, PersistentAgentSystemStep
                        step = PersistentAgentStep.objects.create(
                            agent=agent,
                            description=f"User provided {updated_count} requested credential(s)"
                        )
                        PersistentAgentSystemStep.objects.create(
                            step=step,
                            code=PersistentAgentSystemStep.Code.CREDENTIALS_PROVIDED,
                            notes=f"Secrets provided: {updated_count}"
                        )
                        from api.agent.tasks.process_events import process_agent_events_task
                        transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.pk)))
                        Analytics.track_event(
                            user_id=self.request.user.id,
                            event=AnalyticsEvent.PERSISTENT_AGENT_SECRETS_PROVIDED,
                            source=AnalyticsSource.WEB,
                            properties={
                                'agent_id': str(agent.pk),
                                'agent_name': agent.name,
                                'secrets_provided': updated_count,
                            },
                        )
                        return redirect('agent_secrets_request_thanks', pk=agent.pk)
                    else:
                        messages.info(request, "No changes detected. Enter values to save or remove requests you no longer need.")
            except Exception as e:
                logger.error(f"Failed to update requested secrets for agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to save secrets. Please try again.")

        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)


class AgentSecretRerequestView(LoginRequiredMixin, View):
    """Mark a fulfilled secret as requested again and clear its stored value."""
    def post(self, request, *args, **kwargs):
        agent = get_object_or_404(PersistentAgent, pk=self.kwargs['pk'], user=request.user)
        secret_id = self.kwargs.get('secret_id')
        from api.models import PersistentAgentSecret
        try:
            with transaction.atomic():
                secret = PersistentAgentSecret.objects.get(agent=agent, pk=secret_id)
                secret.requested = True
                secret.encrypted_value = b''
                secret.save(update_fields=['requested', 'encrypted_value', 'updated_at'])
            messages.success(request, f"Re-requested '{secret.name}'. A new value is now required.")
        except PersistentAgentSecret.DoesNotExist:
            messages.error(request, "Secret not found.")
        except Exception as e:
            logger.error(f"Failed to re-request secret {secret_id} for agent {agent.id}: {e}")
            messages.error(request, "Failed to re-request secret.")
        return redirect('agent_secrets', pk=agent.pk)


class AgentSecretsRequestThanksView(LoginRequiredMixin, TemplateView):
    """Thank you page after providing secret values."""
    template_name = "console/agent_secrets_request_thanks.html"

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request Thanks View - get_object")
    def get_object(self):
        """Get the agent or raise 404."""
        return get_object_or_404(
            PersistentAgent,
            pk=self.kwargs['pk'],
            user=self.request.user
        )

    @tracer.start_as_current_span("CONSOLE Agent Secrets Request Thanks View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent to context."""
        context = super().get_context_data(**kwargs)
        context['agent'] = self.get_object()
        return context

class AgentWelcomeView(LoginRequiredMixin, DetailView):
    """Welcome page shown immediately after creating an agent."""
    model = PersistentAgent
    template_name = "console/agent_welcome.html"
    context_object_name = "agent"
    pk_url_kwarg = "pk"

    @tracer.start_as_current_span("CONSOLE Agent Welcome View - get_queryset")
    def get_queryset(self):
        # Ensure users can only access their own agents
        return super().get_queryset().filter(user=self.request.user)

    @tracer.start_as_current_span("CONSOLE Agent Welcome View - get_context_data")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agent = self.get_object()

        # Show agent endpoints for each channel if they exist, regardless of primary flag
        primary_email = agent.comms_endpoints.filter(
            channel=CommsChannel.EMAIL
        ).first()
        primary_sms = agent.comms_endpoints.filter(
            channel=CommsChannel.SMS
        ).first()

        context['primary_email'] = primary_email
        context['primary_sms'] = primary_sms

        # Determine the user's preferred contact channel from the agent's preference
        preferred_channel = None
        try:
            preferred_ep = agent.preferred_contact_endpoint
            if preferred_ep and preferred_ep.channel in (CommsChannel.SMS, CommsChannel.EMAIL):
                preferred_channel = 'sms' if preferred_ep.channel == CommsChannel.SMS else 'email'
        except Exception:
            preferred_channel = None
        # Fallback to detect a likely preference if not set
        if preferred_channel is None:
            if primary_sms and getattr(primary_sms, 'is_primary', False):
                preferred_channel = 'sms'
            elif primary_email and getattr(primary_email, 'is_primary', False):
                preferred_channel = 'email'
        context['preferred_channel'] = preferred_channel

        return context

class AgentContactRequestsView(LoginRequiredMixin, TemplateView):
    """View for displaying and approving contact requests from agents."""
    template_name = "console/agent_contact_requests.html"
    
    def _resolve_agent_or_issue(self):
        """Return (agent, issue) where issue is one of: None, 'invalid', 'wrong_account'."""
        pk = self.kwargs['pk']
        current_span = trace.get_current_span()
        agent = PersistentAgent.objects.filter(pk=pk).select_related('user').first()

        if not agent:
            if current_span:
                current_span.set_attribute("approval.issue", "invalid")
            logger.info("Agent contact-requests invalid agent id", extra={"agent_id": str(pk)})
            return None, 'invalid'

        if agent.user != self.request.user:
            if current_span:
                current_span.set_attribute("approval.issue", "wrong_account")
            logger.info("Agent contact-requests wrong account", extra={"agent_id": str(pk), "user_id": self.request.user.id})
            return None, 'wrong_account'
            
        return agent, None

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get")
    def get(self, request, *args, **kwargs):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='view', issue=issue)
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get_object")
    def get_object(self):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            # Should have been handled in get/post, but keep safety net
            raise Http404("Agent not available")
        return agent
    
    @tracer.start_as_current_span("CONSOLE Agent Contact Requests View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent and pending contact requests to context."""
        context = super().get_context_data(**kwargs)
        agent = self.get_object()
        context['agent'] = agent
        
        # Get pending contact requests
        from api.models import CommsAllowlistRequest, CommsAllowlistEntry, AgentAllowlistInvite
        pending_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).order_by('-requested_at')
        
        context['pending_requests'] = pending_requests
        context['has_pending_requests'] = pending_requests.exists()
        
        # Get current allowlist usage for limit display
        from util.subscription_helper import get_user_max_contacts_per_agent
        max_contacts = get_user_max_contacts_per_agent(agent.user)
        active_count = CommsAllowlistEntry.objects.filter(
            agent=agent, is_active=True
        ).count()
        pending_invites = AgentAllowlistInvite.objects.filter(
            agent=agent, status=AgentAllowlistInvite.InviteStatus.PENDING
        ).count()
        
        context['max_contacts'] = max_contacts
        context['active_count'] = active_count
        context['pending_invites'] = pending_invites
        context['total_count'] = active_count + pending_invites
        context['remaining_slots'] = max(0, max_contacts - (active_count + pending_invites))
        
        # Create form
        from console.forms import ContactRequestApprovalForm
        context['form'] = ContactRequestApprovalForm(contact_requests=pending_requests)
        
        return context
    
    def post(self, request, *args, **kwargs):
        """Handle approval/rejection of contact requests."""
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='update', issue=issue)

        # Safety: agent is present beyond this point
        # Get pending requests
        from api.models import CommsAllowlistRequest, PersistentAgentStep, PersistentAgentSystemStep
        pending_requests = CommsAllowlistRequest.objects.filter(
            agent=agent,
            status=CommsAllowlistRequest.RequestStatus.PENDING
        ).order_by('-requested_at')
        
        from console.forms import ContactRequestApprovalForm
        form = ContactRequestApprovalForm(request.POST, contact_requests=pending_requests)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    approved_count = 0
                    rejected_count = 0
                    approved_addresses = []
                    invitations_sent = []
                    
                    for request_obj in pending_requests:
                        field_name = f'approve_{request_obj.id}'
                        should_approve = form.cleaned_data.get(field_name, False)
                        
                        try:
                            if should_approve:
                                # Get the direction settings from the form
                                inbound_field = f'inbound_{request_obj.id}'
                                outbound_field = f'outbound_{request_obj.id}'
                                allow_inbound = form.cleaned_data.get(inbound_field, True)
                                allow_outbound = form.cleaned_data.get(outbound_field, True)
                                
                                # Update the request's direction settings before approving
                                request_obj.request_inbound = allow_inbound
                                request_obj.request_outbound = allow_outbound
                                request_obj.save(update_fields=['request_inbound', 'request_outbound'])
                                
                                # Try to approve (will directly add to allowlist, skipping invitation)
                                result = request_obj.approve(invited_by=request.user, skip_invitation=True)
                                approved_count += 1
                                approved_addresses.append(f"{request_obj.name or request_obj.address}")
                                
                                # Check if we created a new invitation that needs email (won't happen with skip_invitation=True)
                                from api.models import AgentAllowlistInvite
                                if isinstance(result, AgentAllowlistInvite):
                                    invitations_sent.append(request_obj.address)
                            else:
                                request_obj.reject()
                                rejected_count += 1
                        except ValidationError as e:
                            # Hit the limit, show error
                            messages.error(
                                request, 
                                f"Could not approve {request_obj.address}: {e.message if hasattr(e, 'message') else str(e)}"
                            )
                            continue
                    
                    if approved_count > 0:
                        # Switch agent to manual allowlist mode if not already
                        if agent.whitelist_policy != PersistentAgent.WhitelistPolicy.MANUAL:
                            agent.whitelist_policy = PersistentAgent.WhitelistPolicy.MANUAL
                            agent.save(update_fields=['whitelist_policy'])
                        
                        # Send invitation emails for new invitations
                        if invitations_sent:
                            from django.core.mail import send_mail
                            from django.template.loader import render_to_string
                            from django.urls import reverse
                            from api.models import AgentAllowlistInvite, CommsChannel
                            
                            for address in invitations_sent:
                                # Get the invitation we just created
                                invitation = AgentAllowlistInvite.objects.filter(
                                    agent=agent,
                                    address=address,
                                    status=AgentAllowlistInvite.InviteStatus.PENDING
                                ).first()
                                
                                if invitation and invitation.channel == 'email':
                                    try:
                                        # Get the agent's primary email endpoint
                                        primary_email = agent.comms_endpoints.filter(
                                            channel=CommsChannel.EMAIL, is_primary=True
                                        ).first()
                                        
                                        if not primary_email:
                                            primary_email = agent.comms_endpoints.filter(
                                                channel=CommsChannel.EMAIL
                                            ).first()
                                        
                                        if primary_email:
                                            # Build accept/reject URLs
                                            accept_url = request.build_absolute_uri(
                                                reverse('agent_allowlist_invite_accept', kwargs={'token': invitation.token})
                                            )
                                            reject_url = request.build_absolute_uri(
                                                reverse('agent_allowlist_invite_reject', kwargs={'token': invitation.token})
                                            )
                                            
                                            context = {
                                                'agent': agent,
                                                'agent_owner': agent.user,
                                                'contact_email': address,
                                                'agent_email': primary_email.address,
                                                'accept_url': accept_url,
                                                'reject_url': reject_url,
                                                'invite': invitation,
                                            }
                                            
                                            subject = f"You're invited to communicate with {agent.name} on Gobii"
                                            text_body = render_to_string('emails/agent_allowlist_invite.txt', context)
                                            html_body = render_to_string('emails/agent_allowlist_invite.html', context)
                                            
                                            send_mail(
                                                subject,
                                                text_body,
                                                None,  # Use default from email
                                                [address],
                                                html_message=html_body,
                                                fail_silently=True,  # Don't fail the whole process if email fails
                                            )
                                    except Exception as e:
                                        import logging
                                        logger = logging.getLogger(__name__)
                                        logger.warning("Failed to send allowlist invitation email to %s: %s", address, e)
                        
                        # Create system step to record approvals
                        step = PersistentAgentStep.objects.create(
                            agent=agent,
                            description=f"User approved {approved_count} contact request(s)"
                        )
                        PersistentAgentSystemStep.objects.create(
                            step=step,
                            code=PersistentAgentSystemStep.Code.CONTACTS_APPROVED,
                            notes=f"Approved: {', '.join(approved_addresses)}"
                        )
                        
                        # Trigger agent event processing
                        from api.agent.tasks.process_events import process_agent_events_task
                        transaction.on_commit(lambda: process_agent_events_task.delay(str(agent.pk)))
                        
                        Analytics.track_event(
                            user_id=self.request.user.id,
                            event=AnalyticsEvent.AGENT_CONTACTS_APPROVED,
                            source=AnalyticsSource.WEB,
                            properties={
                                'agent_id': str(agent.pk),
                                'agent_name': agent.name,
                                'approved_count': approved_count,
                                'rejected_count': rejected_count,
                                'invitations_sent': len(invitations_sent),
                            }
                        )
                        
                        # Success message for approved contacts
                        messages.success(
                            request, 
                            f"Successfully approved {approved_count} contact(s) - added to allowlist."
                        )
                    
                    if rejected_count > 0:
                        messages.info(request, f"Rejected {rejected_count} contact(s)")
                    
                    if approved_count > 0 or rejected_count > 0:
                        return redirect('agent_contact_requests_thanks', pk=agent.pk)
                    else:
                        messages.warning(request, "No contacts were selected")
                        
            except Exception as e:
                logger.error(f"Failed to process contact requests for agent {agent.id}: {str(e)}")
                messages.error(request, "Failed to process requests. Please try again.")
        
        # If form invalid or failed, redisplay
        context = self.get_context_data(**kwargs)
        context['form'] = form
        return self.render_to_response(context)

    def _issue_response(self, request, action: str, issue: str, extra: dict | None = None):
        ctx = {
            'issue': issue,
            'context_type': 'agent_allowlist',
            'action': action,
        }
        if extra:
            ctx.update(extra)
        return render(request, "console/approval_link_issue.html", ctx, status=200)


class AgentContactRequestsThanksView(LoginRequiredMixin, TemplateView):
    """Thank you page after approving contact requests."""
    template_name = "console/agent_contact_requests_thanks.html"
    
    def _resolve_agent_or_issue(self):
        pk = self.kwargs['pk']
        current_span = trace.get_current_span()
        exists = PersistentAgent.objects.filter(pk=pk).exists()
        if not exists:
            if current_span:
                current_span.set_attribute("approval.issue", "invalid")
            logger.info("Agent contact-requests-thanks invalid agent id", extra={"agent_id": str(pk)})
            return None, 'invalid'
        agent = PersistentAgent.objects.filter(pk=pk, user=self.request.user).first()
        if not agent:
            if current_span:
                current_span.set_attribute("approval.issue", "wrong_account")
            logger.info("Agent contact-requests-thanks wrong account", extra={"agent_id": str(pk), "user_id": self.request.user.id})
            return None, 'wrong_account'
        return agent, None

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests Thanks View - get")
    def get(self, request, *args, **kwargs):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            return self._issue_response(request, action='view', issue=issue)
        return super().get(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Agent Contact Requests Thanks View - get_object")
    def get_object(self):
        agent, issue = self._resolve_agent_or_issue()
        if issue:
            raise Http404("Agent not available")
        return agent

    def _issue_response(self, request, action: str, issue: str, extra: dict | None = None):
        ctx = {
            'issue': issue,
            'context_type': 'agent_allowlist',
            'action': action,
        }
        if extra:
            ctx.update(extra)
        return render(request, "console/approval_link_issue.html", ctx, status=200)
    
    @tracer.start_as_current_span("CONSOLE Agent Contact Requests Thanks View - get_context_data")
    def get_context_data(self, **kwargs):
        """Add agent to context."""
        context = super().get_context_data(**kwargs)
        context['agent'] = self.get_object()
        return context

@tracer.start_as_current_span("CONSOLE Profile - handle_send_verification")
def handle_send_verification(request, phone):
    """
    Handle sending verification code

    This function checks if the user has an unverified phone number and attempts to send a verification code. If the
    phone number is already verified or does not exist, it shows an error message.

    """
    if not phone:
        return JsonResponse({
            'success': False,
            'error': "No phone number found to send verification code."
        })

    try:
        # Send verification SMS
        with traced("CONSOLE Profile - Twilio - SMS Verification Send"):
            sms.start_verification(phone)

        logger.info(f"Verification code sent to user {request.user.id}")

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.SMS_VERIFICATION_CODE_SENT,
            source=AnalyticsSource.WEB,
            properties={
                'phone_number': phone,
                'user_id': request.user.id,
            }
        )

        return JsonResponse({
            'success': True,
            'message': f"Verification code sent to {phone}"
        })

    except Exception as e:
        logger.error(f"Failed to send verification code for user {request.user.id}: {str(e)}")

    # If we're here, something went wrong
    return JsonResponse({
        'success': False,
        'error': "Failed to send verification code. Please try again."
    })

@tracer.start_as_current_span("CONSOLE Profile - handle_resend_verification")
def handle_resend_verification(request, phone_number):
    """
    Handle resending verification code

    This function checks if the user has an unverified phone number and attempts to resend the verification code. If the
    phone number is already verified or does not exist, it shows an error message.

    """
    try:
        # Send verification SMS
        with traced("CONSOLE Profile - Twilio - SMS Verification Send"):
            sms.start_verification(phone_number)

        logger.info(f"Verification code resent to user {request.user.id}")

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.SMS_RESEND_VERIFICATION_CODE,
            source=AnalyticsSource.WEB,
            properties={
                'phone_number': phone_number,
                'user_id': request.user.id,
            }
        )

        return JsonResponse({
            'success': True,
            'message': f"Verification code resent to {phone_number}"
        })

    except Exception as e:
        logger.error(f"Failed to resend verification code for user {request.user.id}: {str(e)}")
        messages.error(request, "Failed to send verification code. Please try again.")

    # If we're here, something went wrong
    return JsonResponse({
        'success': False,
        'error': "Failed to resend verification code. Please try again."
    })

@tracer.start_as_current_span("CONSOLE Profile - handle_delete_phone")
def handle_delete_phone(request):
    """
    Handle deleting phone number

    This function checks if the user has a phone number and attempts to delete it. If the phone number does not exist,
    it shows an error message. If deletion is successful, it shows a success message.
    """
    try:
        # Get the user's phone number
        phone = UserPhoneNumber.objects.get(user=request.user)

        if not phone:
            logger.warning(f"User {request.user.id} has no phone number but requested to delete it.")
            return JsonResponse({
                'success': False,
                'error': "No phone number found to delete."
            })

        phone.delete()
        logger.info(f"Phone number deleted for user {request.user.id}")

        Analytics.identify(
            user_id=request.user.id,
            traits={
                'has_phone': False,
                'phone_verified': False,
            }
        )

        Analytics.track_event(
            user_id=request.user.id,
            event=AnalyticsEvent.SMS_DELETED,
            source=AnalyticsSource.WEB,
            properties={
                'user_id': request.user.id,
            }
        )

        return JsonResponse({
            'success': True,
            'message': "Phone number deleted successfully."
        })

    except Exception as e:
        logger.error(f"Failed to delete phone number for user {request.user.id}: {str(e)}")
        messages.error(request, "Failed to delete phone number. Please try again.")

    # If we're here, something went wrong
    return JsonResponse({
        'success': False,
        'error': "Failed to delete phone number. Please try again."
    })

@tracer.start_as_current_span("CONSOLE Profile - handle_profile_update")
def handle_profile_update(request, user, phone):
    """Handle normal profile and phone form submission"""
    profile_form = UserProfileForm(request.POST, instance=user)
    phone_form = UserPhoneNumberForm(request.POST, user=user)

    profile_valid = profile_form.is_valid()

    if profile_valid:
        try:
            # Save profile changes
            profile_form.save()

            # Handle phone number changes
            phone_number = phone_form.cleaned_data.get('phone_number')
            verification_code = phone_form.cleaned_data.get('verification_code')

            messages.success(request, "Profile updated successfully!")
            return redirect('console:profile')

        except Exception as e:
            logger.error(f"Error updating profile for user {user.id}: {str(e)}")
            messages.error(request, "An error occurred while updating your profile.")

    # Form validation failed - redisplay with errors
    context = {
        "profile_form": profile_form,
        "phone_form": phone_form,
        "phone": phone,
    }

    return render(request, "console/profile.html", context)

@tracer.start_as_current_span("CONSOLE Profile - handle_confirm_code")
def handle_confirm_code(request, phone_number, verification_code):
    """
    Handle confirming verification code

    This function checks if the user has an unverified phone number and attempts to confirm the verification code.
    If the phone number is already verified or does not exist, it shows an error message.
    """
    if not verification_code:
        return JsonResponse({
            'success': False,
            'error': "Verification code is required."
        })

    try:
        check = False

        with traced("CONSOLE Profile - Twilio - SMS Code Verification"):
            check = sms.check_verification(phone_number, verification_code)

        if check:
            # If the phone number is verified, update the UserPhoneNumber model
            phone, created = UserPhoneNumber.objects.get_or_create(
                user=request.user,
                phone_number=phone_number,
                defaults={
                    'is_verified': True,
                    'is_primary': True,  # Set as primary if it's a new phone, and we only support one phone *for now*
                    'verified_at': timezone.now(),
                    'created_at': timezone.now(),
                    'updated_at': timezone.now(),
                }
            )

            Analytics.identify(
                user_id=request.user.id,
                traits={
                    'has_phone': True,
                    'phone_verified': True,
                }
            )

            Analytics.track_event(
                user_id=request.user.id,
                event=AnalyticsEvent.SMS_VERIFIED,
                source=AnalyticsSource.WEB,
                properties={
                    'phone_number': phone_number,
                    'user_id': request.user.id,
                }
            )

            return JsonResponse({'success': True, 'message': "Phone number verified successfully!"})
        else:
            return JsonResponse({'success': False, 'error': "Invalid verification code. Please try again."})

    except Exception as e:
        logger.warning(f"Failed to confirm verification code for user {request.user.id}: {str(e)}")

    return JsonResponse({'success': False, 'error': "Failed to confirm verification code. Please try again."})


class OrganizationListView(WaffleFlagMixin, ConsoleViewMixin, TemplateView):
    """List organizations the user belongs to."""

    waffle_flag = ORGANIZATIONS
    template_name = "console/organizations.html"

    @tracer.start_as_current_span("CONSOLE Organization List")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        memberships = (
            OrganizationMembership.objects.filter(
                user=self.request.user,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            )
            .select_related("org")
            .order_by("org__name")
        )
        context["memberships"] = memberships
        # Pending invitations for the current user's email
        now = timezone.now()
        pending_invites = (
            OrganizationInvite.objects.filter(
                email__iexact=self.request.user.email,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gte=now,
            )
            .select_related("org", "invited_by")
            .order_by("org__name")
        )
        context["pending_invites"] = pending_invites
        return context


class OrganizationCreateView(WaffleFlagMixin, ConsoleViewMixin, TemplateView):
    """Create a new organization."""

    waffle_flag = ORGANIZATIONS
    template_name = "console/organization_create.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = OrganizationForm()
        return context

    @tracer.start_as_current_span("CONSOLE Organization Create")
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = OrganizationForm(request.POST)
        if form.is_valid():
            org = form.save(commit=False)
            org.slug = slugify(org.name)
            org.created_by = request.user
            org.save()
            OrganizationMembership.objects.create(
                org=org,
                user=request.user,
                role=OrganizationMembership.OrgRole.OWNER,
            )
            messages.success(request, "Organization created successfully.")
            return redirect("organization_detail", org_id=org.id)
        return render(request, self.template_name, {"form": form})


class OrganizationDetailView(WaffleFlagMixin, ConsoleViewMixin, TemplateView):
    """Display organization details and members."""

    waffle_flag = ORGANIZATIONS
    template_name = "console/organization_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.org = get_object_or_404(Organization, id=kwargs["org_id"])
        if not OrganizationMembership.objects.filter(
            org=self.org, user=request.user, status=OrganizationMembership.OrgStatus.ACTIVE
        ).exists():
            return HttpResponseForbidden()
        # Set console context to this organization when visiting its page directly
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(self.org.id)
        request.session['context_name'] = self.org.name
        request.session.modified = True
        return super().dispatch(request, *args, **kwargs)

    @tracer.start_as_current_span("CONSOLE Organization Detail")
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.GET.get("seats_success"):
            messages.success(
                self.request,
                "Seat checkout started successfully. Features will unlock once payment completes.",
            )
        if self.request.GET.get("seats_cancelled"):
            messages.info(
                self.request,
                "Seat checkout was cancelled before completion.",
            )

        members = OrganizationMembership.objects.filter(
            org=self.org, status=OrganizationMembership.OrgStatus.ACTIVE
        ).select_related("user")
        # Pending invites for this organization
        now = timezone.now()
        org_pending_invites = (
            OrganizationInvite.objects.filter(
                org=self.org,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gte=now,
            ).select_related("invited_by")
        )
        billing = getattr(self.org, "billing", None)

        # Determine viewer's membership and allowed role choices for UI
        my_membership = OrganizationMembership.objects.filter(
            org=self.org,
            user=self.request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).first()
        can_manage_members = my_membership and my_membership.role in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
        )
        can_manage_billing = my_membership and my_membership.role in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
            OrganizationMembership.OrgRole.BILLING,
        )
        all_role_choices = list(OrganizationMembership.OrgRole.choices)
        if my_membership and my_membership.role == OrganizationMembership.OrgRole.OWNER:
            allowed_role_choices = all_role_choices
        elif my_membership and my_membership.role == OrganizationMembership.OrgRole.ADMIN:
            allowed_role_choices = [c for c in all_role_choices if c[0] != OrganizationMembership.OrgRole.OWNER]
        else:
            allowed_role_choices = []

        seat_purchase_required = bool(billing and billing.purchased_seats <= 0)

        context.update(
            {
                "org": self.org,
                "members": members,
                "invite_form": OrganizationInviteForm(),
                "pending_invites": org_pending_invites,
                "can_manage_members": bool(can_manage_members),
                "can_manage_billing": bool(can_manage_billing),
                "allowed_role_choices": allowed_role_choices,
                "is_org_owner": bool(my_membership and my_membership.role == OrganizationMembership.OrgRole.OWNER),
                "is_org_admin": bool(my_membership and my_membership.role == OrganizationMembership.OrgRole.ADMIN),
                "org_billing": billing,
                "seat_purchase_required": seat_purchase_required,
                "seat_purchase_form": OrganizationSeatPurchaseForm(org=self.org),
            }
        )
        return context

    @tracer.start_as_current_span("CONSOLE Organization Invite")
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = OrganizationInviteForm(request.POST, org=self.org)
        if form.is_valid():
            invite = OrganizationInvite.objects.create(
                org=self.org,
                email=form.cleaned_data["email"],
                role=form.cleaned_data["role"],
                token=uuid.uuid4().hex,
                expires_at=timezone.now() + timedelta(days=7),
                invited_by=request.user,
            )
            # Send invitation email
            try:
                accept_url = request.build_absolute_uri(
                    reverse("org_invite_accept", kwargs={"token": invite.token})
                )
                reject_url = request.build_absolute_uri(
                    reverse("org_invite_reject", kwargs={"token": invite.token})
                )
                context = {
                    "org": self.org,
                    "invited_by": request.user,
                    "invite": invite,
                    "accept_url": accept_url,
                    "reject_url": reject_url,
                }
                html_body = render_to_string("emails/organization_invite.html", context)
                text_body = render_to_string("emails/organization_invite.txt", context)
                subject = f"You're invited to join {self.org.name} on Gobii"
                send_mail(
                    subject,
                    text_body,
                    None,
                    [invite.email],
                    html_message=html_body,
                    fail_silently=False,
                )
            except Exception as e:
                logger.warning("Failed sending org invite email: %s", e)
            messages.success(request, "Invite sent.")
            return redirect("organization_detail", org_id=self.org.id)
        members = OrganizationMembership.objects.filter(
            org=self.org, status=OrganizationMembership.OrgStatus.ACTIVE
        ).select_related("user")
        now = timezone.now()
        org_pending_invites = (
            OrganizationInvite.objects.filter(
                org=self.org,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gte=now,
            ).select_related("invited_by")
        )
        my_membership = OrganizationMembership.objects.filter(
            org=self.org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        ).first()
        can_manage_members = my_membership and my_membership.role in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
        )
        can_manage_billing = my_membership and my_membership.role in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
            OrganizationMembership.OrgRole.BILLING,
        )
        return render(
            request,
            self.template_name,
            {
                "org": self.org,
                "members": members,
                "invite_form": form,
                "pending_invites": org_pending_invites,
                "org_billing": getattr(self.org, "billing", None),
                "seat_purchase_required": bool(getattr(self.org.billing, "purchased_seats", 0) <= 0),
                "seat_purchase_form": OrganizationSeatPurchaseForm(org=self.org),
                "can_manage_members": bool(can_manage_members),
                "can_manage_billing": bool(can_manage_billing),
            },
        )


class OrganizationInviteValidationMixin:
    """Shared validation helpers for organization invite accept/reject flows."""

    def _resolve_invite_or_issue(self, request, token: str):
        """
        Returns (invite, issue, extra_ctx).
        - invite: OrganizationInvite or None
        - issue: one of None | 'invalid' | 'expired' | 'wrong_account'
        - extra_ctx: dict with optional org/invited_email/invited_by
        """
        invite = (
            OrganizationInvite.objects.select_related("org", "invited_by")
            .filter(token=token)
            .first()
        )
        current_span = trace.get_current_span()
        if not invite:
            logger.info("Organization invite token not found", extra={"token": token})
            if current_span:
                current_span.set_attribute("invite.issue", "invalid_token")
            return None, "invalid", {}

        # Expired or finalized
        if (
            invite.accepted_at is not None
            or invite.revoked_at is not None
            or invite.expires_at < timezone.now()
        ):
            logger.info(
                "Organization invite expired or not valid",
                extra={"org_id": str(invite.org_id), "token": token},
            )
            if current_span:
                current_span.set_attribute("invite.issue", "expired_or_finalized")
            return invite, "expired", {
                "org": invite.org,
                "invited_email": invite.email,
                "invited_by": invite.invited_by,
            }

        # Wrong account/session
        if not request.user.email or invite.email.lower() != request.user.email.lower():
            logger.info(
                "Organization invite wrong account/session",
                extra={"expected_email": invite.email, "actual_email": request.user.email},
            )
            if current_span:
                current_span.set_attribute("invite.issue", "wrong_account")
            return invite, "wrong_account", {
                "org": invite.org,
                "invited_email": invite.email,
                "invited_by": invite.invited_by,
            }

        return invite, None, {}


class OrganizationInviteAcceptView(OrganizationInviteValidationMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Accept an organization invite by token and join the org."""

    waffle_flag = ORGANIZATIONS

    def _accept(self, request, token: str):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            ctx = {"issue": issue, "context_type": "organization_invite", "action": "accept"}
            ctx.update(extra)
            return render(request, "console/approval_link_issue.html", ctx, status=200)

        # Set console context to the invited organization for continuity
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(invite.org.id)
        request.session['context_name'] = invite.org.name
        request.session.modified = True

        # Create or reactivate membership
        membership, created = OrganizationMembership.objects.get_or_create(
            org=invite.org,
            user=request.user,
            defaults={
                "role": invite.role,
                "status": OrganizationMembership.OrgStatus.ACTIVE,
            },
        )
        if not created:
            # If membership already exists, reactivate and/or update role if necessary.
            if membership.status != OrganizationMembership.OrgStatus.ACTIVE or membership.role != invite.role:
                membership.status = OrganizationMembership.OrgStatus.ACTIVE
                membership.role = invite.role
                membership.save(update_fields=["status", "role"])

        invite.accepted_at = timezone.now()
        invite.save(update_fields=["accepted_at"])
        messages.success(request, f"Joined {invite.org.name}.")
        return redirect("organization_detail", org_id=invite.org.id)

    @tracer.start_as_current_span("CONSOLE Organization Invite Accept")
    @transaction.atomic
    def post(self, request, token: str):
        return self._accept(request, token)

    @tracer.start_as_current_span("CONSOLE Organization Invite Accept")
    @transaction.atomic
    def get(self, request, token: str):
        return self._accept(request, token)


class OrganizationInviteRejectView(OrganizationInviteValidationMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Reject an organization invite by token."""

    waffle_flag = ORGANIZATIONS

    def _reject(self, request, token: str):
        invite, issue, extra = self._resolve_invite_or_issue(request, token)
        if issue:
            ctx = {"issue": issue, "context_type": "organization_invite", "action": "reject"}
            ctx.update(extra)
            return render(request, "console/approval_link_issue.html", ctx, status=200)

        # Set console context to the invite's organization for continuity
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(invite.org.id)
        request.session['context_name'] = invite.org.name
        request.session.modified = True

        if invite.accepted_at is None and invite.revoked_at is None:
            invite.revoked_at = timezone.now()
            invite.save(update_fields=["revoked_at"])
            messages.info(request, "Invitation declined.")
        else:
            # Should not hit due to resolver, but keep safety
            return render(request, "console/approval_link_issue.html", {
                "issue": "expired",
                "context_type": "organization_invite",
                "action": "reject",
                "org": invite.org,
                "invited_email": invite.email,
                "invited_by": invite.invited_by,
            }, status=200)
        return redirect("organizations")

    @tracer.start_as_current_span("CONSOLE Organization Invite Reject")
    @transaction.atomic
    def post(self, request, token: str):
        return self._reject(request, token)

    @tracer.start_as_current_span("CONSOLE Organization Invite Reject")
    @transaction.atomic
    def get(self, request, token: str):
        return self._reject(request, token)


class OrganizationSeatCheckoutView(WaffleFlagMixin, LoginRequiredMixin, View):
    """Kick off Stripe Checkout to purchase seats for an organization."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Seat Checkout")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization.objects.select_related("billing"), id=org_id)

        membership = OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=(
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.BILLING,
            ),
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        form = OrganizationSeatPurchaseForm(request.POST, org=org)
        if not form.is_valid():
            for error in form.errors.get("seats", []):
                messages.error(request, error)
            return redirect("organization_detail", org_id=org.id)

        billing = getattr(org, "billing", None)
        seat_count = form.cleaned_data["seats"]
        if seat_count <= 0:
            messages.error(request, "Please select at least one seat to purchase.")
            return redirect("organization_detail", org_id=org.id)

        stripe_settings = get_stripe_settings()
        seat_price_id = stripe_settings.org_team_price_id

        if billing and getattr(billing, "stripe_subscription_id", None):
            # Organization already has an active subscription; treat the input as
            # additional seats to add to the existing licensed item quantity.
            try:
                stripe.api_key = PaymentsHelper.get_stripe_key()
                subscription = stripe.Subscription.retrieve(
                    billing.stripe_subscription_id,
                    expand=["items.data.price"],
                )

                licensed_item = None
                for item in subscription.get("items", {}).get("data", []) or []:
                    price = item.get("price", {}) or {}
                    price_usage_type = price.get("usage_type") or (price.get("recurring", {}) or {}).get("usage_type")
                    price_id = price.get("id")
                    if price_usage_type == "licensed" or (seat_price_id and price_id == seat_price_id):
                        licensed_item = item
                        break

                if not licensed_item:
                    messages.error(
                        request,
                        "We couldn't find a seat item on the active subscription. Please contact support.",
                    )
                    return redirect("organization_detail", org_id=org.id)

                current_quantity = int(licensed_item.get("quantity") or 0)
                if current_quantity < 0:
                    current_quantity = 0
                new_quantity = current_quantity + seat_count

                stripe.Subscription.modify(
                    subscription.get("id"),
                    items=[
                        {
                            "id": licensed_item.get("id"),
                            "quantity": new_quantity,
                        }
                    ],
                    metadata={
                        **(subscription.get("metadata") or {}),
                        "seat_requestor_id": str(request.user.id),
                    },
                    proration_behavior="create_prorations",
                )

                messages.success(
                    request,
                    "Seat update submitted. Additional seats will activate once Stripe processes the change.",
                )
            except Exception as exc:
                logger.exception(
                    "Failed to update Stripe subscription %s for org %s: %s",
                    getattr(billing, "stripe_subscription_id", None),
                    org.id,
                    exc,
                )
                messages.error(
                    request,
                    "We weren't able to update the seat count. Please try again or contact support.",
                )

            return redirect("organization_detail", org_id=org.id)

        price_id = seat_price_id
        if not price_id:
            messages.error(request, "Stripe price not configured. Please contact support.")
            return redirect("organization_detail", org_id=org.id)

        try:
            stripe.api_key = PaymentsHelper.get_stripe_key()
            customer = get_or_create_stripe_customer(org)

            success_url = request.build_absolute_uri(
                reverse("organization_detail", kwargs={"org_id": org.id})
            ) + "?seats_success=1"
            cancel_url = request.build_absolute_uri(
                reverse("organization_detail", kwargs={"org_id": org.id})
            ) + "?seats_cancelled=1"

            line_items = [
                {
                    "price": price_id,
                    "quantity": seat_count,
                }
            ]

            overage_price_id = stripe_settings.org_team_additional_task_price_id
            if overage_price_id:
                line_items.append({"price": overage_price_id})

            session = stripe.checkout.Session.create(
                customer=customer.id,
                api_key=stripe.api_key,
                mode="subscription",
                success_url=success_url,
                cancel_url=cancel_url,
                allow_promotion_codes=True,
                line_items=line_items,
                metadata={
                    "org_id": str(org.id),
                    "seat_requestor_id": str(request.user.id),
                },
            )

            return redirect(session.url)
        except Exception as exc:
            logger.exception("Failed to create Stripe checkout session for org %s: %s", org.id, exc)
            messages.error(
                request,
                "We weren’t able to start the checkout flow. Please try again or contact support.",
            )
            return redirect("organization_detail", org_id=org.id)


class OrganizationSeatPortalView(WaffleFlagMixin, LoginRequiredMixin, View):
    """Open the Stripe billing portal to manage existing organization seats."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Seat Portal")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization.objects.select_related("billing"), id=org_id)

        membership = OrganizationMembership.objects.filter(
            org=org,
            user=request.user,
            status=OrganizationMembership.OrgStatus.ACTIVE,
            role__in=(
                OrganizationMembership.OrgRole.OWNER,
                OrganizationMembership.OrgRole.ADMIN,
                OrganizationMembership.OrgRole.BILLING,
            ),
        ).first()

        if membership is None:
            return HttpResponseForbidden()

        billing = getattr(org, "billing", None)
        if not billing or not billing.stripe_customer_id:
            messages.error(request, "This organization does not have an active Stripe subscription yet.")
            return redirect("organization_detail", org_id=org.id)

        try:
            stripe.api_key = PaymentsHelper.get_stripe_key()

            return_url = request.build_absolute_uri(
                reverse("organization_detail", kwargs={"org_id": org.id})
            )

            session = stripe.billing_portal.Session.create(
                customer=billing.stripe_customer_id,
                api_key=stripe.api_key,
                return_url=return_url,
            )

            return redirect(session.url)
        except Exception as exc:
            logger.exception("Failed to create Stripe billing portal session for org %s: %s", org.id, exc)
            messages.error(
                request,
                "We weren’t able to open the Stripe billing portal. Please try again or contact support.",
            )
            return redirect("organization_detail", org_id=org.id)


class _OrgPermissionMixin:
    """Utilities for checking org membership/role permissions."""

    def _require_org_admin(self, request, org: Organization):
        try:
            membership = OrganizationMembership.objects.get(org=org, user=request.user)
        except OrganizationMembership.DoesNotExist:
            return None
        if membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            return None
        # Allow OWNER and ADMIN to manage invites
        if membership.role not in (
            OrganizationMembership.OrgRole.OWNER,
            OrganizationMembership.OrgRole.ADMIN,
        ):
            return None
        return membership


class OrganizationInviteRevokeOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Revoke a pending invite from the org detail page."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Invite Revoke (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, token: str):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        if not self._require_org_admin(request, org):
            return HttpResponseForbidden()

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at:
            messages.error(request, "Invite is already finalized.")
        else:
            invite.revoked_at = timezone.now()
            invite.save(update_fields=["revoked_at"])
            messages.success(request, "Invitation revoked.")
        return redirect("organization_detail", org_id=org.id)


class OrganizationInviteResendOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Resend a pending invite email from the org detail page."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Invite Resend (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, token: str):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        if not self._require_org_admin(request, org):
            return HttpResponseForbidden()

        invite = get_object_or_404(OrganizationInvite, org=org, token=token)
        if invite.accepted_at or invite.revoked_at or invite.expires_at < timezone.now():
            messages.error(request, "Cannot resend: invite is no longer valid.")
            return redirect("organization_detail", org_id=org.id)

        try:
            accept_url = request.build_absolute_uri(
                reverse("org_invite_accept", kwargs={"token": invite.token})
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
                subject,
                text_body,
                None,
                [invite.email],
                html_message=html_body,
                fail_silently=False,
            )
            messages.success(request, "Invitation email resent.")
        except Exception as e:
            logger.warning("Failed resending org invite email: %s", e)
            messages.error(request, "Failed to resend invitation email.")

        return redirect("organization_detail", org_id=org.id)


class OrganizationMemberRemoveOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Remove a member from an organization (mark membership removed)."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Member Remove (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, user_id: int):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        acting_membership = self._require_org_admin(request, org)
        if not acting_membership:
            return HttpResponseForbidden()

        # Prevent removing self via this action
        if request.user.id == user_id:
            messages.error(request, "You cannot remove yourself.")
            return redirect("organization_detail", org_id=org.id)

        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
        )

        if target_membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            messages.info(request, "This member is already removed.")
            return redirect("organization_detail", org_id=org.id)

        # Admins cannot remove owners
        if (
            acting_membership.role == OrganizationMembership.OrgRole.ADMIN
            and target_membership.role == OrganizationMembership.OrgRole.OWNER
        ):
            return HttpResponseForbidden()

        # Do not remove the last owner
        if target_membership.role == OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You must keep at least one owner in the organization.")
                return redirect("organization_detail", org_id=org.id)

        target_membership.status = OrganizationMembership.OrgStatus.REMOVED
        target_membership.save(update_fields=["status"])
        messages.success(request, "Member removed.")
        return redirect("organization_detail", org_id=org.id)


class OrganizationLeaveOrgView(WaffleFlagMixin, LoginRequiredMixin, View):
    """Allow a user to leave an organization, with safeguards."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Leave (Org)")
    @transaction.atomic
    def post(self, request, org_id: str):
        org = get_object_or_404(Organization, id=org_id)
        # Ensure context is set to this org for the operation
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        try:
            membership = OrganizationMembership.objects.get(org=org, user=request.user)
        except OrganizationMembership.DoesNotExist:
            return HttpResponseForbidden()

        if membership.status != OrganizationMembership.OrgStatus.ACTIVE:
            messages.info(request, "You are not an active member of this organization.")
            return redirect("organizations")

        # Prevent leaving if this is the last remaining owner
        if membership.role == OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You are the last owner. Transfer ownership or add another owner before leaving.")
                return redirect("organization_detail", org_id=org.id)

        membership.status = OrganizationMembership.OrgStatus.REMOVED
        membership.save(update_fields=["status"])
        # After leaving, reset context back to personal
        request.session['context_type'] = 'personal'
        request.session['context_id'] = str(request.user.id)
        request.session['context_name'] = request.user.get_full_name() or request.user.username
        request.session.modified = True
        messages.success(request, f"You left {org.name}.")
        return redirect("organizations")


class OrganizationMemberRoleUpdateOrgView(_OrgPermissionMixin, WaffleFlagMixin, LoginRequiredMixin, View):
    """Change a member's role within an org with basic guardrails."""

    waffle_flag = ORGANIZATIONS

    @tracer.start_as_current_span("CONSOLE Organization Member Role Update (Org)")
    @transaction.atomic
    def post(self, request, org_id: str, user_id: int):
        org = get_object_or_404(Organization, id=org_id)
        # Set context to this organization
        request.session['context_type'] = 'organization'
        request.session['context_id'] = str(org.id)
        request.session['context_name'] = org.name
        request.session.modified = True
        acting_membership = self._require_org_admin(request, org)
        if not acting_membership:
            return HttpResponseForbidden()

        new_role = request.POST.get("role")
        valid_roles = {choice[0] for choice in OrganizationMembership.OrgRole.choices}
        if new_role not in valid_roles:
            return HttpResponseForbidden()

        target_membership = get_object_or_404(
            OrganizationMembership,
            org=org,
            user_id=user_id,
            status=OrganizationMembership.OrgStatus.ACTIVE,
        )

        # No-op
        if target_membership.role == new_role:
            messages.info(request, "Role unchanged.")
            return redirect("organization_detail", org_id=org.id)

        # Admins cannot modify Owners, nor assign Owner role
        if acting_membership.role == OrganizationMembership.OrgRole.ADMIN:
            if target_membership.role == OrganizationMembership.OrgRole.OWNER:
                return HttpResponseForbidden()
            if new_role == OrganizationMembership.OrgRole.OWNER:
                return HttpResponseForbidden()

        # Prevent demoting the last Owner
        if target_membership.role == OrganizationMembership.OrgRole.OWNER and new_role != OrganizationMembership.OrgRole.OWNER:
            active_owner_count = OrganizationMembership.objects.filter(
                org=org,
                role=OrganizationMembership.OrgRole.OWNER,
                status=OrganizationMembership.OrgStatus.ACTIVE,
            ).count()
            if active_owner_count <= 1:
                messages.error(request, "You must keep at least one owner in the organization.")
                return redirect("organization_detail", org_id=org.id)

        target_membership.role = new_role
        target_membership.save(update_fields=["role"])
        messages.success(request, "Member role updated.")
        return redirect("organization_detail", org_id=org.id)


class AgentAllowlistInviteAcceptView(TemplateView):
    """Handle accepting an agent allowlist invitation."""
    template_name = "console/agent_allowlist_invite_response.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        token = kwargs.get("token")
        
        try:
            # Use select_related and prefetch_related for efficiency
            invite = AgentAllowlistInvite.objects.select_related('agent__user').prefetch_related('agent__comms_endpoints').get(token=token)
            context["invite"] = invite
            context["agent"] = invite.agent
            
            if invite.status != AgentAllowlistInvite.InviteStatus.PENDING:
                context["already_responded"] = True
                context["status"] = invite.get_status_display()
            elif invite.is_expired():
                context["expired"] = True
            else:
                context["can_accept"] = True
                
        except AgentAllowlistInvite.DoesNotExist:
            context["invalid_token"] = True
            
        return context
    
    def post(self, request, *args, **kwargs):
        token = kwargs.get("token")
        
        try:
            invite = AgentAllowlistInvite.objects.get(token=token)
            
            if not invite.can_be_accepted():
                messages.error(request, "This invitation is no longer valid.")
                return redirect("agent_allowlist_invite_accept", token=token)
            
            # Accept the invitation
            invite.accept()
            messages.success(
                request, 
                f"Great! You can now communicate with {invite.agent.name} by email."
            )
            
        except AgentAllowlistInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
        except Exception as e:
            messages.error(request, f"Error accepting invitation: {e}")
            
        return redirect("agent_allowlist_invite_accept", token=token)


class AgentAllowlistInviteRejectView(TemplateView):
    """Handle rejecting an agent allowlist invitation.""" 
    template_name = "console/agent_allowlist_invite_response.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        token = kwargs.get("token")
        
        try:
            # Use select_related and prefetch_related for efficiency
            invite = AgentAllowlistInvite.objects.select_related('agent__user').prefetch_related('agent__comms_endpoints').get(token=token)
            context["invite"] = invite
            context["agent"] = invite.agent
            context["rejecting"] = True
            
            if invite.status != AgentAllowlistInvite.InviteStatus.PENDING:
                context["already_responded"] = True  
                context["status"] = invite.get_status_display()
            elif invite.is_expired():
                context["expired"] = True
            else:
                context["can_reject"] = True
                
        except AgentAllowlistInvite.DoesNotExist:
            context["invalid_token"] = True
            
        return context
    
    def post(self, request, *args, **kwargs):
        token = kwargs.get("token")
        
        try:
            invite = AgentAllowlistInvite.objects.get(token=token)
            
            if invite.status != AgentAllowlistInvite.InviteStatus.PENDING:
                messages.error(request, "This invitation has already been responded to.")
                return redirect("agent_allowlist_invite_reject", token=token)
            
            # Reject the invitation
            invite.reject()
            messages.success(request, "You have declined the invitation.")
            
        except AgentAllowlistInvite.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
        except Exception as e:
            messages.error(request, f"Error rejecting invitation: {e}")
            
        return redirect("agent_allowlist_invite_reject", token=token)
