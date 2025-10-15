import logging
import uuid

import djstripe
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.contrib.sites.models import Site
from django.db.models import Count  # For annotated counts
from django.db.models.expressions import OuterRef, Exists

from util.analytics import Analytics, AnalyticsEvent, AnalyticsSource
from api.agent.tasks import process_agent_events_task
from .admin_forms import TestSmsForm, GrantPlanCreditsForm, GrantCreditsByUserIdsForm, AgentEmailAccountForm, StripeConfigForm
from .models import (
    ApiKey, UserQuota, TaskCredit, BrowserUseAgent, BrowserUseAgentTask, BrowserUseAgentTaskStep, PaidPlanIntent,
    DecodoCredential, DecodoIPBlock, DecodoIP, ProxyServer, DedicatedProxyAllocation, ProxyHealthCheckSpec, ProxyHealthCheckResult,
    PersistentAgent, PersistentAgentTemplate, PersistentAgentCommsEndpoint, PersistentAgentMessage, PersistentAgentMessageAttachment, PersistentAgentConversation,
    AgentPeerLink, AgentCommPeerState,
    PersistentAgentStep, PersistentAgentPromptArchive, CommsChannel, UserBilling, OrganizationBilling, SmsNumber, LinkShortener,
    AgentFileSpace, AgentFileSpaceAccess, AgentFsNode, Organization, CommsAllowlistEntry,
    AgentEmailAccount, ToolFriendlyName, TaskCreditConfig, ToolCreditCost,
    StripeConfig,
    MeteringBatch,
    UsageThresholdSent,
)
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse, path
from django.utils.html import format_html
from django.http import HttpResponseRedirect, FileResponse, StreamingHttpResponse
from django.template.response import TemplateResponse
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db.models import Sum
from .agent.files.filespace_service import enqueue_import_after_commit
from .tasks import sync_ip_block, backfill_missing_proxy_records, proxy_health_check_single, garbage_collect_timed_out_tasks
from .tasks.sms_tasks import sync_twilio_numbers, send_test_sms
from config import settings

from djstripe.models import Customer, BankAccount, Card
from djstripe.admin import StripeModelAdmin  # base admin with actions & changelist_view

import zstandard as zstd

# Replace dj-stripe's default registration
# 2.10.1 has removed some fields we still want to see, but their own admin still references them
admin.site.unregister(Customer)

@admin.register(Customer)
class PatchedCustomerAdmin(StripeModelAdmin):
    # remove the removed field; keep valid FKs
    list_select_related = ("subscriber", "djstripe_owner_account", "default_payment_method")

# --- BankAccount ---
admin.site.unregister(BankAccount)

@admin.register(BankAccount)
class PatchedBankAccountAdmin(StripeModelAdmin):
    # DO NOT include 'customer__default_source' (removed in 2.10)
    # Keep the common useful relations for query perf:
    list_select_related = ("customer", "djstripe_owner_account")

# --- Card ---
admin.site.unregister(Card)

@admin.register(Card)
class PatchedCardAdmin(StripeModelAdmin):
    # Valid relations only; 'customer__default_source' was removed in 2.10
    list_select_related = ("customer", "djstripe_owner_account")


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = (
        "prefix",
        "owner_display",
        "name",
        "created_by",
        "created_at",
        "revoked_at",
        "last_used_at",
    )
    search_fields = ("user__email", "organization__name", "prefix", "name")
    list_filter = ("organization",)
    readonly_fields = ("prefix", "hashed_key", "created_at", "last_used_at")

    @admin.display(description="Owner")
    def owner_display(self, obj):
        if obj.organization_id:
            return obj.organization
        return obj.user

@admin.register(UserQuota)
class UserQuotaAdmin(admin.ModelAdmin):
    list_display = ("user", "agent_limit")
    search_fields = ("user__email", "user__id")


@admin.register(StripeConfig)
class StripeConfigAdmin(admin.ModelAdmin):
    form = StripeConfigForm
    list_display = ("release_env", "live_mode", "updated_at")
    search_fields = ("release_env",)
    list_filter = ("live_mode",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "webhook_secret_status",
    )

    fieldsets = (
        (None, {"fields": ("release_env", "live_mode")} ),
        (
            "Secrets",
            {
                "fields": (
                    "webhook_secret",
                    "clear_webhook_secret",
                    "webhook_secret_status",
                )
            },
        ),
        (
            "Identifiers",
            {
                "fields": (
                    "startup_price_id",
                    "startup_additional_task_price_id",
                    "startup_product_id",
                    "startup_dedicated_ip_product_id",
                    "startup_dedicated_ip_price_id",
                    "org_team_product_id",
                    "org_team_price_id",
                    "org_team_additional_task_price_id",
                    "org_team_dedicated_ip_product_id",
                    "org_team_dedicated_ip_price_id",
                    "task_meter_id",
                    "task_meter_event_name",
                    "org_task_meter_id",
                    "org_team_task_meter_id",
                    "org_team_task_meter_event_name",
                )
            },
        ),
        ("Metadata", {"fields": ("created_at", "updated_at")}),
    )

    def webhook_secret_status(self, obj):
        return "Configured" if obj.has_value("webhook_secret") else "Not set"

    webhook_secret_status.short_description = "Webhook secret"


# Ownership filter reused across models
class OwnershipTypeFilter(SimpleListFilter):
    title = 'Ownership'
    parameter_name = 'ownership'

    def lookups(self, request, model_admin):
        return (
            ('user', 'User-owned'),
            ('org', 'Org-owned'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'user':
            return queryset.filter(organization__isnull=True)
        if self.value() == 'org':
            return queryset.filter(organization__isnull=False)
        return queryset


class SoftExpirationFilter(SimpleListFilter):
    title = 'Soft-expiration'
    parameter_name = 'soft_expired'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Expired'),
            ('no', 'Not expired'),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if val == 'yes':
            return queryset.filter(life_state='expired')
        if val == 'no':
            return queryset.exclude(life_state='expired')
        return queryset


# --- TASK CREDIT ADMIN (Optimized) ---
@admin.register(TaskCredit)
class TaskCreditAdmin(admin.ModelAdmin):
    list_display = (
        "owner_display",
        "credits",
        "credits_used",
        "available_credits",
        "plan",
        "grant_type",
        "granted_date",
        "expiration_date",
        "additional_task",
        "voided",
    )
    list_filter = [
        OwnershipTypeFilter,
        "plan",
        "additional_task",
        "expiration_date",
        "granted_date",
        "grant_type",
        "voided",
    ]
    search_fields = ("user__email", "stripe_invoice_id", "user__id", "organization__name", "organization__id")
    readonly_fields = ("id", "stripe_invoice_id")
    raw_id_fields = ("user", "organization")
    ordering = ("-granted_date",)

    # Performance: avoid an extra query per row for the user column.
    list_select_related = ("user", "organization")

    # UX: allow quick navigation via calendar drill-down
    date_hierarchy = "granted_date"
    change_list_template = "admin/taskcredit_change_list.html"

    @admin.display(description='Owner')
    def owner_display(self, obj):
        if obj.organization_id:
            return f"Org: {obj.organization.name} ({obj.organization_id})"
        if obj.user_id:
            return f"User: {obj.user.email} ({obj.user_id})"
        return "-"

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        term = (search_term or "").strip()
        if term.isdigit():
            try:
                queryset = queryset | self.model.objects.filter(user_id=int(term))
                use_distinct = True
            except ValueError:
                # The term is numeric but not a valid integer (e.g., too large),
                # so we skip the exact user ID search. The default search might still find it.
                pass
        return queryset, use_distinct

    # ---------------- Custom admin view: Grant by Plan -----------------
    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path(
                'grant-by-plan/',
                self.admin_site.admin_view(self.grant_by_plan_view),
                name='api_taskcredit_grant_by_plan',
            ),
            path(
                'grant-by-user-ids/',
                self.admin_site.admin_view(self.grant_by_user_ids_view),
                name='api_taskcredit_grant_by_user_ids',
            ),
        ]
        return custom + urls

    def grant_by_plan_view(self, request):
        from django.template.response import TemplateResponse
        from django.contrib import messages
        from django.db import transaction
        from django.utils import timezone
        from django.apps import apps
        from constants.plans import PlanNamesChoices

        if not request.user.has_perm("api.add_taskcredit"):
            messages.error(request, "You do not have permission to grant task credits.")
            return HttpResponseRedirect(reverse("admin:api_taskcredit_changelist"))

        form = GrantPlanCreditsForm(request.POST or None)
        context = dict(self.admin_site.each_context(request))
        context.update({
            "opts": self.model._meta,
            "title": "Grant Credits by Plan",
            "form": form,
        })

        if request.method == "POST" and form.is_valid():
            plan = form.cleaned_data["plan"]
            credits = form.cleaned_data["credits"]
            grant_type = form.cleaned_data["grant_type"]
            grant_date = form.cleaned_data["grant_date"]
            expiration_date = form.cleaned_data["expiration_date"]
            dry_run = form.cleaned_data["dry_run"]
            only_zero = form.cleaned_data["only_if_out_of_credits"]
            export_csv = form.cleaned_data["export_csv"]

            # Resolve model lazily to avoid import cycles
            TaskCredit = apps.get_model("api", "TaskCredit")
            User = get_user_model()
            from util.subscription_helper import get_user_plan
            from constants.grant_types import GrantTypeChoices

            # Iterate active users and match plan
            matched_users = []
            for user in User.objects.filter(is_active=True).iterator():
                try:
                    up = get_user_plan(user)
                    if up and up.get("id") == plan:
                        matched_users.append(user)
                except Exception as e:
                    logging.warning("Failed to get plan for user %s: %s", user.id, e)
                    continue

            # Optionally filter to users currently out of credits
            if only_zero:
                from django.db.models import Sum, Q, Value
                from django.db.models.functions import Coalesce

                now = timezone.now()
                user_ids = [user.id for user in matched_users]

                users_with_zero_credits_ids = set(
                    User.objects.filter(id__in=user_ids)
                    .annotate(
                        available_credits_sum=Coalesce(
                            Sum(
                                "task_credits__available_credits",
                                filter=Q(
                                    task_credits__granted_date__lte=now,
                                    task_credits__expiration_date__gte=now,
                                    task_credits__voided=False,
                                ),
                            ),
                            Value(0),
                        )
                    )
                    .filter(available_credits_sum__lte=0)
                    .values_list('id', flat=True)
                )

                matched_users = [user for user in matched_users if user.id in users_with_zero_credits_ids]

            # Dry-run CSV export
            if dry_run and export_csv:
                import csv
                from django.http import HttpResponse
                from django.db.models import Sum
                from util.subscription_helper import get_user_plan
                now = timezone.now().strftime('%Y%m%d_%H%M%S')
                filename = f"grant_by_plan_dry_run_{plan}_{now}.csv"
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                writer = csv.writer(response)
                writer.writerow(["user_id", "email", "plan_id", "available_credits"])
                for user in matched_users:
                    total = TaskCredit.objects.filter(
                        user=user,
                        granted_date__lte=timezone.now(),
                        expiration_date__gte=timezone.now(),
                        voided=False,
                    ).aggregate(s=Sum('available_credits'))['s'] or 0
                    up = None
                    try:
                        up = get_user_plan(user)
                    except Exception:
                        up = None
                    plan_id = (up.get('id') if isinstance(up, dict) else None) or ''
                    writer.writerow([str(user.id), user.email or '', plan_id, total])
                return response

            created = 0
            if not dry_run:
                with transaction.atomic():
                    for user in matched_users:
                        TaskCredit.objects.create(
                            user=user,
                            credits=credits,
                            credits_used=0,
                            granted_date=grant_date,
                            expiration_date=expiration_date,
                            plan=PlanNamesChoices(plan),
                            grant_type=grant_type,
                            additional_task=False,
                            voided=False,
                        )
                        created += 1
                messages.success(request, f"Granted {credits} credits to {created} users on plan '{plan}'.")
            else:
                messages.info(request, f"Dry-run: would grant {credits} credits to {len(matched_users)} users on plan '{plan}'.")

            return HttpResponseRedirect(reverse("admin:api_taskcredit_changelist"))

        return TemplateResponse(request, "admin/grant_plan_credits.html", context)

    def grant_by_user_ids_view(self, request):
        from django.template.response import TemplateResponse
        from django.contrib import messages
        from django.db import transaction
        from django.utils import timezone
        from django.apps import apps

        if not request.user.has_perm("api.add_taskcredit"):
            messages.error(request, "You do not have permission to grant task credits.")
            return HttpResponseRedirect(reverse("admin:api_taskcredit_changelist"))

        form = GrantCreditsByUserIdsForm(request.POST or None)
        context = dict(self.admin_site.each_context(request))
        context.update({
            "opts": self.model._meta,
            "title": "Grant Credits to User IDs",
            "form": form,
        })

        if request.method == "POST" and form.is_valid():
            raw = form.cleaned_data['user_ids']
            credits = form.cleaned_data['credits']
            selected_plan = form.cleaned_data['plan']
            grant_type = form.cleaned_data['grant_type']
            grant_date = form.cleaned_data['grant_date']
            expiration_date = form.cleaned_data['expiration_date']
            dry_run = form.cleaned_data['dry_run']
            only_zero = form.cleaned_data['only_if_out_of_credits']
            export_csv = form.cleaned_data['export_csv']

            # Parse IDs by commas or newlines
            import re
            ids = [s for s in re.split(r"[\s,]+", raw.strip()) if s]

            TaskCredit = apps.get_model("api", "TaskCredit")
            User = get_user_model()
            from constants.plans import PlanNamesChoices

            # ids are integers; invalid tokens are ignored by the filter
            users = list(User.objects.filter(id__in=ids, is_active=True))

            if only_zero:
                from django.db.models import Sum, Q, Value
                from django.db.models.functions import Coalesce

                now = timezone.now()
                user_ids = [user.id for user in users]

                users_with_zero_credits_ids = set(
                    User.objects.filter(id__in=user_ids)
                    .annotate(
                        available_credits_sum=Coalesce(
                            Sum(
                                "task_credits__available_credits",
                                filter=Q(
                                    task_credits__granted_date__lte=now,
                                    task_credits__expiration_date__gte=now,
                                    task_credits__voided=False,
                                ),
                            ),
                            Value(0),
                        )
                    )
                    .filter(available_credits_sum__lte=0)
                    .values_list('id', flat=True)
                )

                users = [user for user in users if user.id in users_with_zero_credits_ids]

            # Dry-run CSV export
            if dry_run and export_csv:
                import csv
                from django.http import HttpResponse
                from django.db.models import Sum
                from util.subscription_helper import get_user_plan
                now = timezone.now().strftime('%Y%m%d_%H%M%S')
                filename = f"grant_by_user_ids_dry_run_{now}.csv"
                response = HttpResponse(content_type='text/csv')
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                writer = csv.writer(response)
                writer.writerow(["user_id", "email", "plan_id", "available_credits"])
                for user in users:
                    total = TaskCredit.objects.filter(
                        user=user,
                        granted_date__lte=timezone.now(),
                        expiration_date__gte=timezone.now(),
                        voided=False,
                    ).aggregate(s=Sum('available_credits'))['s'] or 0
                    up = None
                    try:
                        up = get_user_plan(user)
                    except Exception:
                        up = None
                    plan_id = (up.get('id') if isinstance(up, dict) else None) or ''
                    writer.writerow([str(user.id), user.email or '', plan_id, total])
                return response

            created = 0
            if not dry_run:
                with transaction.atomic():
                    for user in users:
                        # Use the selected plan for the TaskCredit record
                        plan_choice = PlanNamesChoices(selected_plan)
                        TaskCredit.objects.create(
                            user=user,
                            credits=credits,
                            credits_used=0,
                            granted_date=grant_date,
                            expiration_date=expiration_date,
                            plan=plan_choice,
                            grant_type=grant_type,
                            additional_task=False,
                            voided=False,
                        )
                        created += 1
                messages.success(request, f"Granted {credits} credits to {created} users.")
            else:
                messages.info(request, f"Dry-run: would grant {credits} credits to {len(users)} users.")

            return HttpResponseRedirect(reverse("admin:api_taskcredit_changelist"))

        return TemplateResponse(request, "admin/grant_user_ids_credits.html", context)


@admin.register(TaskCreditConfig)
class TaskCreditConfigAdmin(admin.ModelAdmin):
    list_display = ("default_task_cost", "updated_at")
    readonly_fields = ("singleton_id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("default_task_cost",)}),
        ("Metadata", {"fields": ("singleton_id", "created_at", "updated_at")}),
    )

    def has_add_permission(self, request):
        if TaskCreditConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):  # pragma: no cover - defensive guard
        return False


@admin.register(ToolCreditCost)
class ToolCreditCostAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "credit_cost", "updated_at")
    search_fields = ("tool_name",)
    list_filter = ("updated_at",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("tool_name", "credit_cost")}),
        ("Metadata", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(MeteringBatch)
class MeteringBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_key",
        "user",
        "rounded_quantity",
        "total_credits",
        "period_start",
        "period_end",
        "stripe_event_id",
        "created_at",
    )
    search_fields = (
        "batch_key",
        "idempotency_key",
        "stripe_event_id",
        "user__email",
        "user__id",
    )
    list_filter = ("period_start", "period_end", "created_at")
    date_hierarchy = "created_at"
    readonly_fields = ("id", "batch_key", "idempotency_key", "created_at", "updated_at", "usage_links")
    raw_id_fields = ("user",)
    ordering = ("-created_at",)

    @admin.display(description="Usage Rows")
    def usage_links(self, obj):
        try:
            tasks_count = BrowserUseAgentTask.objects.filter(meter_batch_key=obj.batch_key).count()
            steps_count = PersistentAgentStep.objects.filter(meter_batch_key=obj.batch_key).count()
        except Exception:
            tasks_count = 0
            steps_count = 0

        tasks_url = (
            reverse("admin:api_browseruseagenttask_changelist") + f"?meter_batch_key__exact={obj.batch_key}"
        )
        steps_url = (
            reverse("admin:api_persistentagentstep_changelist") + f"?meter_batch_key__exact={obj.batch_key}"
        )
        return format_html(
            '<a href="{}">Tasks: {}</a> &nbsp;|&nbsp; <a href="{}">Steps: {}</a>',
            tasks_url, tasks_count, steps_url, steps_count
        )


# Minimal admin for Organization to enable autocomplete/search
@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    search_fields = ("name", "slug")
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active", "plan")

# --- TASKS INSIDE AGENT (BrowserUseAgent) ---
class BrowserUseAgentTaskInline(admin.TabularInline):
    model = BrowserUseAgentTask
    extra = 0
    fields = ("id", "prompt_summary", "status", "created_at", "view_task_link")
    readonly_fields = ("id", "prompt_summary", "status", "created_at", "view_task_link")
    show_change_link = False # Using a custom link

    # Limit the number of tasks displayed inline to avoid rendering thousands of rows.
    MAX_DISPLAY = 50  # Show the 50 most-recent tasks only

    def get_queryset(self, request):
        """
        Return only the most recent MAX_DISPLAY tasks for the parent agent,
        avoiding issues with admin filtering.
        """
        # Get the full queryset of all tasks
        qs = super().get_queryset(request)
        
        # Extract parent object_id from the URL
        object_id = request.resolver_match.kwargs.get("object_id")
        
        if not object_id:
            # We are on an add page, no parent object yet
            return qs.none()
        
        # Filter tasks for the specific parent agent
        qs = qs.filter(agent__pk=object_id)
        
        # Order by creation date to get the most recent
        qs = qs.order_by('-created_at')
        
        # Get the primary keys of the most recent N tasks
        recent_pks = list(qs.values_list('pk', flat=True)[:self.MAX_DISPLAY])
        
        # Return a new, unsliced queryset filtered by those specific pks.
        # This is the safe way to limit results in an admin inline.
        return self.model.objects.filter(pk__in=recent_pks).order_by('-created_at')

    def prompt_summary(self, obj):
        # obj is BrowserUseAgentTask instance
        if obj.prompt:
            return (obj.prompt[:75] + '...') if len(obj.prompt) > 75 else obj.prompt
        return "-"
    prompt_summary.short_description = "Prompt"

    def view_task_link(self, obj):
        # obj is BrowserUseAgentTask instance
        if obj.pk:
            url = reverse("admin:api_browseruseagenttask_change", args=[obj.pk])
            return format_html('<a href="{}">View/Edit Task</a>', url)
        return "-"
    view_task_link.short_description = "Link to Task"

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# Link to the Task changelist, filtered to this agent, for Agent list display
def tasks_for_agent_link(obj):
    # obj here is a BrowserUseAgent instance
    url = (
        reverse("admin:api_browseruseagenttask_changelist")
        + f"?agent__id__exact={obj.pk}"
    )
    # Prefer annotated count if present to avoid an extra query per row.
    count = getattr(obj, "num_tasks", None)
    if count is None:
        count = obj.tasks.count()
    return format_html('<a href="{}">{} Tasks</a>', url, count)
tasks_for_agent_link.short_description = "Tasks (Filtered List)"

@admin.register(BrowserUseAgent)
class BrowserUseAgentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user_email_display", tasks_for_agent_link, "persistent_agent_link", "created_at", "updated_at")
    search_fields = ("name", "user__email", "id") 
    readonly_fields = ("id", "created_at", "updated_at", "persistent_agent_link", "tasks_summary_link")
    list_filter = ("user",) 
    raw_id_fields = ('user',)
    inlines = [BrowserUseAgentTaskInline] # Added inline for tasks

    # ------------------------------------------------------------------
    # Performance: annotate task counts & use select_related to reduce queries
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user').annotate(num_tasks=Count('tasks'))

    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'name', 'user', 'created_at', 'updated_at')
        }),
        ('Configuration', {
            'fields': ('preferred_proxy',)
        }),
        ('Relationships', {
            'fields': ('persistent_agent_link', 'tasks_summary_link')
        }),
    )

    # ------------------------------------------------------------------
    # Read-only summary + link to full task list (detail page)
    # ------------------------------------------------------------------
    @admin.display(description="Tasks")
    def tasks_summary_link(self, obj):
        url = (
            reverse("admin:api_browseruseagenttask_changelist")
            + f"?agent__id__exact={obj.pk}"
        )

        count = getattr(obj, "num_tasks", None)
        if count is None:
            count = obj.tasks.count()

        return format_html(
            '<a href="{}">View all&nbsp;{} tasks</a>', url, count
        )

    @admin.display(description='User Email')
    def user_email_display(self, obj):
        if obj.user:
            return obj.user.email
        return None # Or some placeholder if user can be None (not in this model)

    @admin.display(description='Persistent Agent')
    def persistent_agent_link(self, obj):
        """Link to the associated persistent agent (if any)."""
        try:
            pa = obj.persistent_agent  # May raise PersistentAgent.DoesNotExist
        except obj._meta.get_field('persistent_agent').related_model.DoesNotExist:  # type: ignore[attr-defined]
            pa = None

        if pa:
            url = reverse("admin:api_persistentagent_change", args=[pa.pk])
            return format_html('<a href="{}">{}</a>', url, pa.name)
        return format_html('<span style="color: gray;">None</span>')
    persistent_agent_link.admin_order_field = 'persistent_agent__name'

# --- STEPS INSIDE TASK (BrowserUseAgentTask) ---
class BrowserUseAgentTaskStepInline(admin.TabularInline):
    model = BrowserUseAgentTaskStep
    extra = 0
    fields = ('step_number', 'description_summary', 'is_result', 'result_value_summary', 'view_step_link')
    readonly_fields = ('step_number', 'description_summary', 'is_result', 'result_value_summary', 'view_step_link')
    show_change_link = False

    # Limit the number of steps displayed inline to avoid rendering thousands of rows.
    MAX_DISPLAY = 50

    def get_queryset(self, request):
        """
        Return only the most recent MAX_DISPLAY steps for the parent task,
        avoiding issues with admin filtering.
        """
        # Get the full queryset of all steps
        qs = super().get_queryset(request)
        
        # Extract parent object_id from the URL
        object_id = request.resolver_match.kwargs.get("object_id")
        
        if not object_id:
            # We are on an add page, no parent object yet
            return qs.none()
            
        # Filter steps for the specific parent task
        qs = qs.filter(task__pk=object_id)
        
        # Order by step number to get the most recent
        qs = qs.order_by('-step_number')
        
        # Get the primary keys of the most recent N steps
        recent_pks = list(qs.values_list('pk', flat=True)[:self.MAX_DISPLAY])
        
        # Return a new, unsliced queryset filtered by those specific pks.
        return self.model.objects.filter(pk__in=recent_pks).order_by('-step_number')

    def description_summary(self, obj):
        if obj.description:
            return (obj.description[:75] + '...') if len(obj.description) > 75 else obj.description
        return "-"
    description_summary.short_description = "Description"

    def result_value_summary(self, obj):
        if obj.result_value:
            # Simple string representation for summary; can be expanded
            value_str = str(obj.result_value)
            return (value_str[:75] + '...') if len(value_str) > 75 else value_str
        return "-"
    result_value_summary.short_description = "Result Value"

    def view_step_link(self, obj):
        if obj.pk:
            url = reverse("admin:api_browseruseagenttaskstep_change", args=[obj.pk])
            return format_html('<a href="{}">View/Edit Step</a>', url)
        return "-"
    view_step_link.short_description = "Link to Step"

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(BrowserUseAgentTask)
class BrowserUseAgentTaskAdmin(admin.ModelAdmin):
    change_list_template = "admin/browseruseagenttask_change_list.html"

    list_display = ('id', 'get_agent_name', 'get_user_email', 'status', 'credits_cost', 'display_task_result_summary', 'created_at', 'updated_at')
    list_filter = ('status', 'user', 'agent', 'meter_batch_key', 'metered')
    search_fields = ('id', 'agent__name', 'user__email')
    readonly_fields = ('id', 'created_at', 'updated_at', 'display_full_task_result', 'credits_cost') # Show charged credits
    raw_id_fields = ('agent', 'user')
    inlines = [BrowserUseAgentTaskStepInline] # Added inline for steps

    def get_queryset(self, request):
        """Optimize with select_related to prevent N+1 queries."""
        qs = super().get_queryset(request)
        return qs.select_related("agent", "user")

    def get_agent_name(self, obj):
        return obj.agent.name if obj.agent else None
    get_agent_name.short_description = 'Agent Name'
    get_agent_name.admin_order_field = 'agent__name'

    def get_user_email(self, obj):
        return obj.user.email if obj.user else None
    get_user_email.short_description = 'User Email'
    get_user_email.admin_order_field = 'user__email'

    def display_task_result_summary(self, obj):
        # obj is BrowserUseAgentTask instance
        result_step = obj.steps.filter(is_result=True).first()
        if result_step:
            if result_step.result_value:
                return format_html("<b>Result:</b> Present <small>(Step {})</small>", result_step.step_number)
            else:
                return format_html("<span style='color: orange;'>Result: Empty (Step {})</span>", result_step.step_number)
        return "No Result Step"
    display_task_result_summary.short_description = "Task Result Summary"

    def display_full_task_result(self, obj):
        # obj is BrowserUseAgentTask instance
        result_step = obj.steps.filter(is_result=True).first()
        if result_step:
            if result_step.result_value:
                import json # For pretty printing
                try:
                    # Attempt to pretty-print if it's JSON, otherwise just stringify
                    pretty_result = json.dumps(result_step.result_value, indent=2, sort_keys=True)
                    return format_html("<pre>Step {}:<br>{}</pre>", result_step.step_number, pretty_result)
                except (TypeError, ValueError):
                     return format_html("Step {}:<br>{}", result_step.step_number, str(result_step.result_value))
            else:
                return format_html("Step {} marked as result, but <code>result_value</code> is empty.", result_step.step_number)
        return "No step is marked as the result for this task."
    display_full_task_result.short_description = "Task Result Details"

    # ------------------------------------------------------------------
    #  Custom view + button: Run Garbage Collection
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'run-gc/',
                self.admin_site.admin_view(self.run_gc_view),
                name='api_browseruseagenttask_run_gc',
            ),
        ]
        return custom_urls + urls

    @admin.display(description="Run GC")
    def run_gc_button(self, obj=None):
        """Display a fixed Run GC button in admin changelist (object-tools)."""
        url = reverse('admin:api_browseruseagenttask_run_gc')
        return format_html('<a class="button" href="{}">🗑️ Run&nbsp;Garbage&nbsp;Collection</a>', url)

    def run_gc_view(self, request, *args, **kwargs):
        """Admin view that queues the garbage collection task and redirects back."""
        try:
            garbage_collect_timed_out_tasks.delay()
            self.message_user(request, "Garbage-collection task queued – refresh in a minute.", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"Error queuing garbage collection: {e}", messages.ERROR)
        # Redirect back to the changelist
        changelist_url = reverse('admin:api_browseruseagenttask_changelist')
        return HttpResponseRedirect(changelist_url)

@admin.register(BrowserUseAgentTaskStep)
class BrowserUseAgentTaskStepAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "step_number", "is_result", "created_at")
    list_filter = ("is_result", "created_at")
    search_fields = ("task__id", "description")
    ordering = ("-created_at",)

@admin.register(PaidPlanIntent)
class PaidPlanIntentAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_name", "requested_at")
    list_filter = ("plan_name", "requested_at")
    search_fields = ("user__email", "user__username")
    ordering = ("-requested_at",)
    readonly_fields = ("requested_at", "id")


@admin.register(UsageThresholdSent)
class UsageThresholdSentAdmin(admin.ModelAdmin):
    list_display = ("user", "period_ym", "threshold", "plan_limit", "sent_at")
    list_filter = ("threshold",)
    search_fields = ("user__email", "user__id", "period_ym")
    date_hierarchy = "sent_at"
    readonly_fields = ("user", "period_ym", "threshold", "plan_limit", "sent_at")
    ordering = ("-sent_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# --- TASK CREDITS INSIDE USER (CustomUserAdmin) ---
class TaskCreditInlineForUser(admin.TabularInline):
    model = TaskCredit
    extra = 0
    fields = ("credits", "credits_used", "remaining_display", "plan", "granted_date", "expiration_date", "additional_task")
    readonly_fields = ("remaining_display", "granted_date")
    ordering = ("-granted_date",)
    
    def remaining_display(self, obj):
        return obj.remaining
    remaining_display.short_description = "Remaining"

# --- AGENTS INSIDE USER (CustomUserAdmin) ---
class BrowserUseAgentInlineForUser(admin.TabularInline):
    model = BrowserUseAgent
    extra = 0
    fields = ("name", "created_at", "tasks_for_this_agent_link", "view_agent_link")
    readonly_fields = ("name", "created_at", "tasks_for_this_agent_link", "view_agent_link")
    show_change_link = False # Using custom link

    def tasks_for_this_agent_link(self, obj):
        # obj here is a BrowserUseAgent instance
        if obj.pk:
            url = (
                reverse("admin:api_browseruseagenttask_changelist")
                + f"?agent__id__exact={obj.pk}"
            )
            return format_html('<a href="{}">View Tasks</a>', url)
        return "N/A (Agent not saved)"
    tasks_for_this_agent_link.short_description = "Tasks"

    def view_agent_link(self, obj):
        # obj here is a BrowserUseAgent instance
        if obj.pk:
            url = reverse("admin:api_browseruseagent_change", args=[obj.pk])
            return format_html('<a href="{}">View/Edit {}</a>', url, obj.name or "Agent")
        return "N/A (Agent not saved)"
    view_agent_link.short_description = "Agent Page"

    def has_add_permission(self, request, obj=None):
        return False 
    def has_delete_permission(self, request, obj=None):
        return False

User = get_user_model()

if admin.site.is_registered(User):
    admin.site.unregister(User)

# ------------------------------------------------------------------
# CUSTOM USER ADMIN (Optimized)  ------------------------------------
# ------------------------------------------------------------------

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Remove the heavy TaskCredit inline and keep the agent inline only.
    inlines = [BrowserUseAgentInlineForUser]

    actions = ['queue_rollup_for_selected_users']

    @admin.action(description="Queue metering rollup for selected users")
    def queue_rollup_for_selected_users(self, request, queryset):
        from api.tasks.billing_rollup import rollup_usage_for_user
        queued = 0
        for user in queryset:
            try:
                rollup_usage_for_user.delay(user.id)
                queued += 1
            except Exception as e:
                logging.error("Failed to queue rollup for user %s: %s", user.id, e)
                continue
        self.message_user(request, f"Queued rollup for {queued} user(s).", level=messages.INFO)

    def get_queryset(self, request):
        """Annotate credit totals to avoid N+1 queries in the changelist."""
        qs = super().get_queryset(request)
        return qs.annotate(
            total_credits=Sum("task_credits__credits"),
            used_credits=Sum("task_credits__credits_used"),
        )


    # Add a summary field for task credits (read-only).
    def get_readonly_fields(self, request, obj=None):
        # Preserve any readonly fields defined by UserAdmin.
        base = super().get_readonly_fields(request, obj)
        return base + ("taskcredit_summary_link",)

    def get_fieldsets(self, request, obj=None):
        # Append a dedicated "Task Credits" fieldset to the default ones.
        fieldsets = list(super().get_fieldsets(request, obj))
        fieldsets.append(("Task Credits", {"fields": ("taskcredit_summary_link",)}))
        return tuple(fieldsets)

    @admin.display(description="Task Credits")
    def taskcredit_summary_link(self, obj):
        """Compact summary + link to full TaskCredit list for this user."""
        # Use annotated values if available (from get_queryset)
        total = getattr(obj, "total_credits", 0) or 0
        used = getattr(obj, "used_credits", 0) or 0
        
        # Fallback to aggregation if not on the changelist view (e.g., on the change form)
        if not hasattr(obj, "total_credits"):
            summary = obj.task_credits.aggregate(
                total=Sum("credits"),
                used=Sum("credits_used"),
            )
            total = summary["total"] or 0
            used = summary["used"] or 0

        remaining = total - used

        url = (
            reverse("admin:api_taskcredit_changelist") + f"?user__id__exact={obj.pk}"
        )
        return format_html(
            "{} total / {} used / {} remaining&nbsp;&nbsp;<a href=\"{}\">View details</a>",
            total,
            used,
            remaining,
            url,
        )


# --- DECODO MODELS ---

class DecodoIPBlockInline(admin.TabularInline):
    model = DecodoIPBlock
    extra = 0
    fields = ('endpoint', 'start_port', 'block_size', 'created_at', 'view_ip_block_link')
    readonly_fields = ('created_at', 'view_ip_block_link')

    def view_ip_block_link(self, obj):
        if obj.pk:
            url = reverse("admin:api_decodoipblock_change", args=[obj.pk])
            return format_html('<a href="{}">View/Edit Block</a>', url)
        return "-"
    view_ip_block_link.short_description = "Block Details"


@admin.register(DecodoCredential)
class DecodoCredentialAdmin(admin.ModelAdmin):
    list_display = ('username', 'ip_blocks_count', 'created_at', 'updated_at')
    search_fields = ('username',)
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = [DecodoIPBlockInline]

    def ip_blocks_count(self, obj):
        return obj.ip_blocks.count()
    ip_blocks_count.short_description = 'IP Blocks'


class DecodoIPInline(admin.TabularInline):
    model = DecodoIP
    extra = 0
    fields = ('ip_address', 'country_name', 'city_name', 'isp_name', 'view_ip_link')
    readonly_fields = ('view_ip_link',)

    def view_ip_link(self, obj):
        if obj.pk:
            url = reverse("admin:api_decodoip_change", args=[obj.pk])
            return format_html('<a href="{}">View/Edit IP</a>', url)
        return "-"
    view_ip_link.short_description = "IP Details"


@admin.register(DecodoIPBlock)
class DecodoIPBlockAdmin(admin.ModelAdmin):
    list_display = ('endpoint', 'start_port', 'block_size', 'credential_username', 'ip_count', 'sync_now', 'created_at')
    list_filter = ('endpoint', 'credential')
    search_fields = ('endpoint', 'credential__username')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('credential',)
    inlines = [DecodoIPInline]

    def get_urls(self):
        """Add custom URL for sync functionality."""
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/sync/',
                self.admin_site.admin_view(self.sync_view),
                name='api_decodoipblock_sync',
            ),
        ]
        return custom_urls + urls

    @admin.display(description="")
    def sync_now(self, obj):
        """Render the sync button for this IP block."""
        url = reverse("admin:api_decodoipblock_sync", args=[obj.pk])
        return format_html('<a class="button" href="{}">Sync&nbsp;Now</a>', url)

    def sync_view(self, request, object_id, *args, **kwargs):
        """Handle the sync button click - queue a Celery task and redirect."""
        try:
            # Verify the object exists
            ip_block = DecodoIPBlock.objects.get(pk=object_id)

            # Queue the sync task
            sync_ip_block.delay(str(ip_block.id))

            # Show success message
            self.message_user(
                request,
                f"Sync queued for IP block {ip_block.endpoint}:{ip_block.start_port}",
                messages.SUCCESS
            )

        except DecodoIPBlock.DoesNotExist:
            self.message_user(
                request,
                "IP block not found",
                messages.ERROR
            )
        except Exception as e:
            self.message_user(
                request,
                f"Error queuing sync: {str(e)}",
                messages.ERROR
            )

        # Redirect back to the change form
        return HttpResponseRedirect(
            reverse("admin:api_decodoipblock_change", args=[object_id])
        )

    def credential_username(self, obj):
        return obj.credential.username if obj.credential else None
    credential_username.short_description = 'Credential'
    credential_username.admin_order_field = 'credential__username'

    def ip_count(self, obj):
        return obj.ip_addresses.count()
    ip_count.short_description = 'IP Count'


@admin.register(DecodoIP)
class DecodoIPAdmin(admin.ModelAdmin):
    list_display = ('ip_address', 'port', 'country_name', 'city_name', 'isp_name', 'ip_block_endpoint', 'created_at')
    list_filter = ('country_code', 'country_name', 'isp_name', 'ip_block__credential')
    search_fields = ('ip_address', 'country_name', 'city_name', 'isp_name', 'ip_block__endpoint')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('ip_block',)

    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'ip_block', 'ip_address', 'port', 'created_at', 'updated_at')
        }),
        ('ISP Information', {
            'fields': ('isp_name', 'isp_asn', 'isp_domain', 'isp_organization')
        }),
        ('Location Information', {
            'fields': ('country_code', 'country_name', 'country_continent', 'city_name', 'city_code', 'city_state', 'city_timezone', 'city_zip_code', 'city_latitude', 'city_longitude')
        }),
    )

    def ip_block_endpoint(self, obj):
        return f"{obj.ip_block.endpoint}:{obj.ip_block.start_port}" if obj.ip_block else None
    ip_block_endpoint.short_description = 'IP Block'
    ip_block_endpoint.admin_order_field = 'ip_block__endpoint'


@admin.register(ProxyServer)
class ProxyServerAdmin(admin.ModelAdmin):
    list_display = ('name', 'proxy_type', 'host', 'port', 'username', 'static_ip', 'is_active', 'is_dedicated', 'health_results_link', 'decodo_ip_link', 'test_now', 'created_at')
    list_filter = ('proxy_type', 'is_active', 'is_dedicated', 'created_at')
    search_fields = ('name', 'host', 'username', 'static_ip', 'notes')
    readonly_fields = ('id', 'created_at', 'updated_at')
    raw_id_fields = ('decodo_ip',)
    fieldsets = (
        ('Details', {
            'fields': (
                'id', 'name', 'proxy_type', 'host', 'port', 'username', 'password', 'static_ip',
                'is_active', 'is_dedicated', 'notes', 'decodo_ip', 'created_at', 'updated_at'
            )
        }),
    )
    
    def get_urls(self):
        """Add custom URL for health check functionality."""
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/health-check/',
                self.admin_site.admin_view(self.health_check_view),
                name='api_proxyserver_health_check',
            ),
        ]
        return custom_urls + urls

    @admin.action(description="Mark selected proxies as dedicated")
    def mark_as_dedicated(self, request, queryset):
        updated = queryset.update(is_dedicated=True)
        self.message_user(request, f"{updated} proxy server(s) marked as dedicated.", level=messages.SUCCESS)

    @admin.action(description="Mark selected proxies as shared")
    def mark_as_shared(self, request, queryset):
        updated = queryset.update(is_dedicated=False)
        self.message_user(request, f"{updated} proxy server(s) marked as shared.", level=messages.SUCCESS)

    @admin.display(description="")
    def test_now(self, obj):
        """Render the health check button for this proxy server."""
        if obj.is_active:
            url = reverse("admin:api_proxyserver_health_check", args=[obj.pk])
            return format_html('<a class="button" href="{}">Test&nbsp;Now</a>', url)
        return format_html('<span style="color: gray;">Inactive</span>')

    def health_check_view(self, request, object_id, *args, **kwargs):
        """Handle the health check button click - queue a Celery task and redirect."""
        try:
            # Verify the object exists
            proxy_server = ProxyServer.objects.get(pk=object_id)

            # Queue the health check task
            proxy_health_check_single.delay(str(proxy_server.id))

            # Show success message
            self.message_user(
                request,
                f"Health check queued for proxy {proxy_server.host}:{proxy_server.port}",
                messages.SUCCESS
            )

        except ProxyServer.DoesNotExist:
            self.message_user(
                request,
                "Proxy server not found",
                messages.ERROR
            )
        except Exception as e:
            self.message_user(
                request,
                f"Error queuing health check: {str(e)}",
                messages.ERROR
            )

        # Redirect back to the change form
        return HttpResponseRedirect(
            reverse("admin:api_proxyserver_change", args=[object_id])
        )
    
    fieldsets = (
        ('Basic Configuration', {
            'fields': ('id', 'name', 'proxy_type', 'host', 'port', 'is_active')
        }),
        ('Authentication', {
            'fields': ('username', 'password'),
            'classes': ('collapse',)
        }),
        ('IP Information', {
            'fields': ('static_ip', 'decodo_ip')
        }),
        ('Metadata', {
            'fields': ('notes', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def health_results_link(self, obj):
        """Link to view health check results for this proxy."""
        count = obj.health_check_results.count()
        if count > 0:
            url = reverse("admin:api_proxyhealthcheckresult_changelist") + f"?proxy_server__id__exact={obj.id}"
            recent_passed = obj.health_check_results.filter(status='PASSED').order_by('-checked_at').first()
            recent_failed = obj.health_check_results.filter(status__in=['FAILED', 'ERROR', 'TIMEOUT']).order_by('-checked_at').first()
            
            # Determine status color
            if recent_passed and (not recent_failed or recent_passed.checked_at > recent_failed.checked_at):
                color = "green"
                icon = "✓"
            elif recent_failed:
                color = "red" 
                icon = "✗"
            else:
                color = "gray"
                icon = "?"
                
            return format_html('<a href="{}" style="color: {};">{} {} results</a>', url, color, icon, count)
        return format_html('<span style="color: gray;">No tests</span>')
    health_results_link.short_description = 'Health Status'

    def decodo_ip_link(self, obj):
        if obj.decodo_ip:
            url = reverse("admin:api_decodoip_change", args=[obj.decodo_ip.pk])
            return format_html('<a href="{}">{}</a>', url, obj.decodo_ip.ip_address)
        return None
    decodo_ip_link.short_description = 'Decodo IP'
    
    actions = ['mark_as_dedicated', 'mark_as_shared', 'backfill_missing_proxies', 'test_selected_proxies']
    
    def backfill_missing_proxies(self, request, queryset):
        """Action to backfill missing proxy records for all Decodo IPs."""
        try:
            backfill_missing_proxy_records.delay()
            self.message_user(
                request,
                "Backfill task queued to create missing proxy records for all Decodo IPs",
                messages.SUCCESS
            )
        except Exception as e:
            self.message_user(
                request,
                f"Error queuing backfill task: {str(e)}",
                messages.ERROR
            )
    backfill_missing_proxies.short_description = "Backfill missing proxy records for Decodo IPs"
    
    def test_selected_proxies(self, request, queryset):
        """Action to run health checks on selected proxy servers."""
        active_proxies = queryset.filter(is_active=True)
        inactive_count = queryset.count() - active_proxies.count()
        
        if not active_proxies.exists():
            self.message_user(
                request,
                "No active proxy servers selected for health check",
                messages.WARNING
            )
            return
        
        try:
            # Queue health check tasks for each selected active proxy
            queued_count = 0
            for proxy in active_proxies:
                proxy_health_check_single.delay(str(proxy.id))
                queued_count += 1
            
            message = f"Health checks queued for {queued_count} proxy server(s)"
            if inactive_count > 0:
                message += f" (skipped {inactive_count} inactive proxy server(s))"
            
            self.message_user(
                request,
                message,
                messages.SUCCESS
            )
        except Exception as e:
            self.message_user(
                request,
                f"Error queuing health checks: {str(e)}",
                messages.ERROR
            )
    test_selected_proxies.short_description = "Run health checks on selected proxy servers"


@admin.register(DedicatedProxyAllocation)
class DedicatedProxyAllocationAdmin(admin.ModelAdmin):
    list_display = ('proxy', 'owner_display', 'allocated_at', 'updated_at')
    list_filter = ('owner_user', 'owner_organization')
    search_fields = (
        'proxy__name',
        'proxy__host',
        'owner_user__email',
        'owner_user__username',
        'owner_organization__name',
    )
    raw_id_fields = ('proxy', 'owner_user', 'owner_organization')
    readonly_fields = ('id', 'allocated_at', 'updated_at')
    ordering = ('-allocated_at',)
    fieldsets = (
        ('Allocation', {
            'fields': ('id', 'proxy', 'allocated_at', 'updated_at')
        }),
        ('Owner', {
            'fields': ('owner_user', 'owner_organization', 'notes')
        }),
    )

    def owner_display(self, obj):
        return obj.owner
    owner_display.short_description = 'Owner'


@admin.register(ProxyHealthCheckSpec)
class ProxyHealthCheckSpecAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'results_count', 'created_at', 'updated_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'prompt')
    readonly_fields = ('id', 'created_at', 'updated_at')
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'name', 'is_active')
        }),
        ('Health Check Configuration', {
            'fields': ('prompt',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def results_count(self, obj):
        """Show the number of health check results for this spec."""
        count = obj.results.count()
        if count > 0:
            url = reverse("admin:api_proxyhealthcheckresult_changelist") + f"?health_check_spec__id__exact={obj.id}"
            return format_html('<a href="{}">{} results</a>', url, count)
        return "0 results"
    results_count.short_description = "Results"


@admin.register(ProxyHealthCheckResult)
class ProxyHealthCheckResultAdmin(admin.ModelAdmin):
    list_display = ('proxy_server_link', 'health_check_spec_link', 'status', 'response_time_ms', 'checked_at', 'created_at')
    list_filter = ('status', 'checked_at', 'health_check_spec', 'proxy_server__proxy_type', 'proxy_server__is_active')
    search_fields = ('proxy_server__name', 'proxy_server__host', 'health_check_spec__name', 'error_message')
    readonly_fields = ('id', 'checked_at', 'created_at')
    raw_id_fields = ('proxy_server', 'health_check_spec')
    date_hierarchy = 'checked_at'
    
    fieldsets = (
        ('Test Information', {
            'fields': ('id', 'proxy_server', 'health_check_spec', 'checked_at')
        }),
        ('Results', {
            'fields': ('status', 'response_time_ms', 'error_message')
        }),
        ('Raw Data', {
            'fields': ('task_result', 'notes'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def proxy_server_link(self, obj):
        """Link to the proxy server being tested."""
        if obj.proxy_server:
            url = reverse("admin:api_proxyserver_change", args=[obj.proxy_server.pk])
            return format_html('<a href="{}">{}</a>', url, f"{obj.proxy_server.host}:{obj.proxy_server.port}")
        return None
    proxy_server_link.short_description = 'Proxy Server'
    proxy_server_link.admin_order_field = 'proxy_server__host'
    
    def health_check_spec_link(self, obj):
        """Link to the health check spec used."""
        if obj.health_check_spec:
            url = reverse("admin:api_proxyhealthcheckspec_change", args=[obj.health_check_spec.pk])
            return format_html('<a href="{}">{}</a>', url, obj.health_check_spec.name)
        return None
    health_check_spec_link.short_description = 'Health Check Spec'
    health_check_spec_link.admin_order_field = 'health_check_spec__name'
    
    def get_queryset(self, request):
        """Optimize queryset with select_related for better performance."""
        return super().get_queryset(request).select_related('proxy_server', 'health_check_spec')


# --- PERSISTENT AGENT MODELS ---

class PersistentAgentCommsEndpointInline(admin.TabularInline):
    """Inline for viewing/editing agent communication endpoints."""
    model = PersistentAgentCommsEndpoint
    extra = 0
    fields = ('channel', 'address', 'is_primary', 'endpoint_link')
    readonly_fields = ('endpoint_link',)
    
    def endpoint_link(self, obj):
        if obj.pk:
            url = reverse("admin:api_persistentagentcommsendpoint_change", args=[obj.pk])
            return format_html('<a href="{}">Edit Details</a>', url)
        return "-"
    endpoint_link.short_description = "Details"

    def has_delete_permission(self, request, obj=None):
        # Allow deletion but be careful not to delete primary endpoints
        return True


class CommsAllowlistEntryInline(admin.TabularInline):
    """Inline to manage manual allowlist entries for an agent."""
    model = CommsAllowlistEntry
    extra = 0
    fields = ("channel", "address", "is_active", "verified")
    readonly_fields = ()
    classes = ("collapse",)


class AgentMessageInline(admin.TabularInline):
    """Inline for viewing agent conversation history."""
    model = PersistentAgentMessage
    fk_name = 'owner_agent'
    extra = 0
    fields = ('timestamp', 'direction_display', 'from_to_display', 'body_preview', 'status_display', 'message_link')
    readonly_fields = ('timestamp', 'direction_display', 'from_to_display', 'body_preview', 'status_display', 'message_link')
    # Show newest messages first so the most relevant information is immediately visible.
    ordering = ('-timestamp',)

    # Limit how many messages we render inline to avoid very large HTML tables that
    # freeze the browser when an agent has thousands of messages.
    MAX_DISPLAY = 50  # Most-recent N messages to show inline

    def get_queryset(self, request):
        """
        Return only the most recent MAX_DISPLAY messages for the parent agent,
        avoiding issues with admin filtering.
        """
        # Get the full queryset of all messages
        qs = super().get_queryset(request)
        
        # Extract parent object_id from the URL, which is how inlines are linked
        object_id = request.resolver_match.kwargs.get("object_id")
        
        if not object_id:
            # We are on an add page, no parent object yet
            return qs.none()
        
        # Filter messages for the specific parent agent
        qs = qs.filter(owner_agent__pk=object_id)
        
        # Order by timestamp to get the most recent
        qs = qs.order_by('-timestamp')
        
        # Get the primary keys of the most recent N messages
        recent_pks = list(qs.values_list('pk', flat=True)[:self.MAX_DISPLAY])
        
        # Return a new, unsliced queryset filtered by those specific pks.
        # This is the safe way to limit results in an admin inline.
        return self.model.objects.filter(pk__in=recent_pks).order_by('-timestamp')

    can_delete = False
    
    def direction_display(self, obj):
        if obj.is_outbound:
            return format_html('<span style="color: blue;">→ OUT</span>')
        else:
            return format_html('<span style="color: green;">← IN</span>')
    direction_display.short_description = "Direction"
    
    def from_to_display(self, obj):
        from_addr = obj.from_endpoint.address if obj.from_endpoint else "Unknown"
        to_addr = obj.to_endpoint.address if obj.to_endpoint else "Conversation"
        return f"{from_addr} → {to_addr}"
    from_to_display.short_description = "From → To"
    
    def body_preview(self, obj):
        if obj.body:
            preview = obj.body.replace('\n', ' ').strip()
            return (preview[:75] + '...') if len(preview) > 75 else preview
        return "-"
    body_preview.short_description = "Message"
    
    def status_display(self, obj):
        status = obj.latest_status
        color_map = {
            'queued': 'orange',
            'sent': 'green', 
            'failed': 'red',
            'delivered': 'blue'
        }
        color = color_map.get(status, 'gray')
        return format_html('<span style="color: {};">{}</span>', color, status.title())
    status_display.short_description = "Status"
    
    def message_link(self, obj):
        if obj.pk:
            url = reverse("admin:api_persistentagentmessage_change", args=[obj.pk])
            return format_html('<a href="{}">View</a>', url)
        return "-"
    message_link.short_description = "Details"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PersistentAgent)
class PersistentAgentAdmin(admin.ModelAdmin):
    change_list_template = "admin/persistentagent_change_list.html"
    list_display = (
        'name', 'user_email', 'ownership_scope', 'organization', 'browser_use_agent_link',
        'is_active', 'execution_environment', 'schedule', 'life_state', 'last_interaction_at',
        'message_count', 'created_at'
    )
    list_filter = (OwnershipTypeFilter, SoftExpirationFilter, 'organization', 'is_active', 'execution_environment', 'schedule', 'created_at')
    search_fields = ('name', 'user__email', 'organization__name', 'charter', 'short_description')
    raw_id_fields = ('user', 'browser_use_agent')
    readonly_fields = (
        'id', 'ownership_scope', 'created_at', 'updated_at',
        'browser_use_agent_link', 'agent_actions', 'messages_summary_link',
        'last_expired_at', 'sleep_email_sent_at',
        'short_description', 'short_description_charter_hash', 'short_description_requested_hash',
    )
    inlines = [PersistentAgentCommsEndpointInline, CommsAllowlistEntryInline, AgentMessageInline]

    # ------------------------------------------------------------------
    # Performance: annotate message counts so we don't do a COUNT query per row
    # ------------------------------------------------------------------
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(num_messages=Count('agent_messages'))
    
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'id', 'name', 'user', 'organization', 'ownership_scope',
                'charter', 'short_description', 'short_description_charter_hash',
                'short_description_requested_hash', 'created_at', 'updated_at',
            )
        }),
        ('Configuration', {
            'fields': ('browser_use_agent', 'browser_use_agent_link', 'schedule', 'is_active', 'execution_environment')
        }),
        ('Soft Expiration (Testing)', {
            'description': 'Override last_interaction_at to simulate inactivity windows. last_expired_at and notices are read-only for audit.',
            'fields': ('life_state', 'last_interaction_at', 'last_expired_at', 'sleep_email_sent_at')
        }),
        ('Actions', {
            'fields': ('agent_actions',)
        }),
        ('Data Links', {
            'fields': ('messages_summary_link',)
        }),
    )
    
    def get_urls(self):
        """Add custom URLs for simulation and processing actions."""
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/simulate-email/',
                self.admin_site.admin_view(self.simulate_email_view),
                name='api_persistentagent_simulate_email',
            ),
            path(
                '<path:object_id>/simulate-sms/',
                self.admin_site.admin_view(self.simulate_sms_view),
                name='api_persistentagent_simulate_sms',
            ),
            path(
                'trigger-processing/',
                self.admin_site.admin_view(self.trigger_processing_view),
                name='api_persistentagent_trigger_processing',
            ),
        ]
        return custom_urls + urls

    @admin.display(description='User Email')
    def user_email(self, obj):
        return obj.user.email

    @admin.display(description='Ownership')
    def ownership_scope(self, obj):
        try:
            if obj and obj.organization:
                return f"Org-owned: {obj.organization.name}"
            if obj:
                return "User-owned (no organization)"
        except Exception:
            pass
        return "User-owned by default unless organization is set"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Hint: name uniqueness depends on owner scope
        name_field = form.base_fields.get('name')
        if name_field:
            extra = (
                "Name must be unique within the selected owner: "
                "user when no organization; organization when set."
            )
            name_field.help_text = f"{name_field.help_text} {extra}" if name_field.help_text else extra

        org_field = form.base_fields.get('organization')
        if org_field:
            extra_org = (
                "Leave blank for user-owned agents; set to make this org-owned."
            )
            org_field.help_text = f"{org_field.help_text} {extra_org}" if org_field.help_text else extra_org
        return form

    @admin.display(description='Browser Use Agent')
    def browser_use_agent_link(self, obj):
        """Link to the associated browser use agent."""
        bua = obj.browser_use_agent  # Direct FK; could be None if allowed
        if bua:
            url = reverse("admin:api_browseruseagent_change", args=[bua.pk])
            return format_html('<a href="{}">{}</a>', url, bua.name)
        return format_html('<span style="color: gray;">None</span>')
    browser_use_agent_link.admin_order_field = 'browser_use_agent__name'

    @admin.display(description='Messages')
    def message_count(self, obj):
        # Prefer the annotated value when available to avoid an extra DB query.
        count = getattr(obj, 'num_messages', None)
        if count is None:
            count = obj.agent_messages.count()

        if count > 0:
            url = reverse("admin:api_persistentagentmessage_changelist") + f"?owner_agent__id__exact={obj.pk}"
            return format_html('<a href="{}">{} messages</a>', url, count)
        return "0 messages"
    
    @admin.display(description="All Messages")
    def messages_summary_link(self, obj):
        """Link to view all messages for this agent in the dedicated admin."""
        url = reverse("admin:api_persistentagentmessage_changelist") + f"?owner_agent__id__exact={obj.pk}"
        
        count = getattr(obj, 'num_messages', None)
        if count is None:
            count = obj.agent_messages.count()
        
        return format_html(
            '<a href="{}">View all {} messages</a>', url, count
        )

    @admin.display(description='Agent Actions')
    def agent_actions(self, obj):
        """Renders action buttons on the agent's detail page."""
        if obj and obj.pk:
            simulate_email_url = reverse("admin:api_persistentagent_simulate_email", args=[obj.pk])
            simulate_sms_url = reverse("admin:api_persistentagent_simulate_sms", args=[obj.pk])
            buttons = f'<a class="button" href="{simulate_email_url}">Simulate Email</a>'
            buttons += f'&nbsp;<a class="button" href="{simulate_sms_url}">Simulate SMS</a>'
            return format_html(buttons)
        return "Save agent to see actions"

    def trigger_processing_view(self, request):
        """Queue event processing for the provided persistent agent IDs."""
        changelist_url = reverse('admin:api_persistentagent_changelist')

        if request.method != 'POST':
            return TemplateResponse(
                request,
                "admin/persistentagent_trigger_processing.html",
                {"title": "Trigger Event Processing", "agent_ids": ""},
            )

        raw_ids = request.POST.get('agent_ids', '')
        parsed_ids: list[str] = []
        invalid_entries: list[str] = []

        for line in raw_ids.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            try:
                parsed_ids.append(str(uuid.UUID(candidate)))
            except (ValueError, TypeError):
                invalid_entries.append(candidate)

        # Deduplicate, preserve order, and check for existence
        unique_ids = list(dict.fromkeys(parsed_ids))
        existing_ids = set(map(str, PersistentAgent.objects.filter(id__in=unique_ids).values_list('id', flat=True)))
        non_existent_ids = [agent_id for agent_id in unique_ids if agent_id not in existing_ids]

        queued = 0
        failures: list[str] = []

        for agent_id in existing_ids:
            try:
                process_agent_events_task.delay(agent_id)
                queued += 1
            except Exception:  # pragma: no cover - defensive logging
                logging.exception("Failed to queue event processing for persistent agent %s", agent_id)
                failures.append(agent_id)

        if queued:
            plural = "s" if queued != 1 else ""
            self.message_user(
                request,
                f"Queued event processing for {queued} persistent agent{plural}.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No persistent agents were queued. Provide at least one valid persistent agent ID.",
                level=messages.WARNING,
            )

        if invalid_entries:
            self.message_user(
                request,
                "Skipped invalid ID(s): " + ", ".join(invalid_entries),
                level=messages.WARNING,
            )

        if failures:
            self.message_user(
                request,
                "Failed to queue ID(s): " + ", ".join(failures),
                level=messages.ERROR,
            )

        return HttpResponseRedirect(changelist_url)

    def simulate_email_view(self, request, object_id):
        """Handle email simulation for an agent."""
        try:
            agent = PersistentAgent.objects.get(pk=object_id)
        except PersistentAgent.DoesNotExist:
            self.message_user(request, "Agent not found", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:api_persistentagent_changelist"))

        if request.method == 'POST':
            from_address = request.POST.get('from_address', '').strip()
            subject = request.POST.get('subject', '').strip()
            body = request.POST.get('body', '').strip()
            attachments = request.FILES.getlist('attachments') if hasattr(request, 'FILES') else []
            
            # Validation
            if not from_address:
                self.message_user(request, "From address is required", messages.ERROR)
            elif not body:
                self.message_user(request, "Message body is required", messages.ERROR)
            else:
                try:
                    # Find agent's primary email endpoint
                    to_endpoint = PersistentAgentCommsEndpoint.objects.filter(
                        owner_agent=agent, 
                        channel=CommsChannel.EMAIL, 
                        is_primary=True
                    ).first()
                    
                    if not to_endpoint:
                        # Fallback to any email endpoint
                        to_endpoint = PersistentAgentCommsEndpoint.objects.filter(
                            owner_agent=agent, 
                            channel=CommsChannel.EMAIL
                        ).first()
                    
                    if not to_endpoint:
                        self.message_user(
                            request, 
                            "Agent has no email address configured. Please add one first.", 
                            messages.ERROR
                        )
                        return HttpResponseRedirect(reverse('admin:api_persistentagent_change', args=[object_id]))
                    
                    # Normalize through the same ingestion pipeline as webhooks
                    from api.agent.comms.adapters import ParsedMessage
                    from api.agent.comms.message_service import ingest_inbound_message

                    parsed = ParsedMessage(
                        sender=from_address,
                        recipient=to_endpoint.address,
                        subject=subject or "",
                        body=body,
                        attachments=list(attachments or []),  # file-like objects supported by ingest
                        raw_payload={"_source": "admin_simulation"},
                        msg_channel=CommsChannel.EMAIL,
                    )

                    msg_info = ingest_inbound_message(CommsChannel.EMAIL, parsed)
                    message = msg_info.message
                    
                    self.message_user(
                        request, 
                        f"Incoming email simulated successfully from {from_address}. "
                        f"Message ID: {message.id}. The agent will react as in production (including wake-up).",
                        messages.SUCCESS
                    )
                    
                except Exception as e:
                    self.message_user(
                        request, 
                        f"Error creating simulated email: {str(e)}", 
                        messages.ERROR
                    )
            
            return HttpResponseRedirect(reverse('admin:api_persistentagent_change', args=[object_id]))
        
        else:
            # Display form
            context = {
                **self.admin_site.each_context(request),
                'agent': agent,
                'title': f'Simulate Incoming Email for {agent.name}',
                'opts': self.model._meta,
            }
            return TemplateResponse(request, "admin/api/persistentagent/simulate_email.html", context)

    def simulate_sms_view(self, request, object_id):
        """Handle SMS simulation for an agent using the same ingestion pipeline as webhooks."""
        try:
            agent = PersistentAgent.objects.get(pk=object_id)
        except PersistentAgent.DoesNotExist:
            self.message_user(request, "Agent not found", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:api_persistentagent_changelist"))

        if request.method == 'POST':
            from_address = request.POST.get('from_address', '').strip()
            body = request.POST.get('body', '').strip()

            # Validation
            if not from_address:
                self.message_user(request, "From address is required", messages.ERROR)
            elif not body:
                self.message_user(request, "Message body is required", messages.ERROR)
            else:
                try:
                    # Find agent's primary email endpoint
                    to_endpoint = PersistentAgentCommsEndpoint.objects.filter(
                        owner_agent=agent,
                        channel=CommsChannel.SMS,
                        is_primary=True
                    ).first()

                    if not to_endpoint:
                        # Fallback to any email endpoint
                        to_endpoint = PersistentAgentCommsEndpoint.objects.filter(
                            owner_agent=agent,
                            channel=CommsChannel.SMS
                        ).first()

                    if not to_endpoint:
                        self.message_user(
                            request,
                            "Agent has no SMS number configured. Please add one first.",
                            messages.ERROR
                        )
                        return HttpResponseRedirect(reverse('admin:api_persistentagent_change', args=[object_id]))

                    # Normalize through the same ingestion pipeline as webhooks
                    from api.agent.comms.adapters import ParsedMessage
                    from api.agent.comms.message_service import ingest_inbound_message

                    parsed = ParsedMessage(
                        sender=from_address,
                        recipient=to_endpoint.address,
                        subject=None,
                        body=body,
                        attachments=[],
                        raw_payload={"_source": "admin_simulation"},
                        msg_channel=CommsChannel.SMS,
                    )
                    msg_info = ingest_inbound_message(CommsChannel.SMS, parsed)
                    message = msg_info.message

                    self.message_user(
                        request,
                        f"Incoming SMS simulated successfully from {from_address}. "
                        f"Message ID: {message.id}. The agent will react as in production (including wake-up).",
                        messages.SUCCESS
                    )

                except Exception as e:
                    self.message_user(
                        request,
                        f"Error creating simulated SMS: {str(e)}",
                        messages.ERROR
                    )

            return HttpResponseRedirect(reverse('admin:api_persistentagent_change', args=[object_id]))

        else:
            # Display form
            context = {
                **self.admin_site.each_context(request),
                'agent': agent,
                'title': f'Simulate Incoming SMS for {agent.name}',
                'opts': self.model._meta,
            }
            return TemplateResponse(request, "admin/api/persistentagent/simulate_sms.html", context)

@admin.register(PersistentAgentCommsEndpoint)
class PersistentAgentCommsEndpointAdmin(admin.ModelAdmin):
    list_display = (
        'address', 'channel', 'owner_agent_name', 'is_primary', 'message_count',
        'test_smtp_button', 'test_imap_button', 'poll_imap_now_button'
    )
    list_filter = ('channel', 'is_primary', 'owner_agent')
    search_fields = ('address', 'owner_agent__name', 'owner_agent__user__email')
    raw_id_fields = ('owner_agent',)
    readonly_fields = ('test_smtp_button',)

    class AgentEmailAccountInline(admin.StackedInline):
        model = AgentEmailAccount
        form = AgentEmailAccountForm
        extra = 0
        can_delete = True
        verbose_name = "Agent Email Account"
        verbose_name_plural = "Agent Email Account"
        fields = (
            # SMTP
            'smtp_host', 'smtp_port', 'smtp_security', 'smtp_auth', 'smtp_username', 'smtp_password', 'is_outbound_enabled',
            # IMAP
            'imap_host', 'imap_port', 'imap_security', 'imap_username', 'imap_password', 'imap_folder', 'is_inbound_enabled', 'imap_idle_enabled', 'poll_interval_sec',
            # Health
            'connection_last_ok_at', 'connection_error',
        )
        readonly_fields = ('connection_last_ok_at', 'connection_error')

        def has_add_permission(self, request, obj):
            # Allow create only for email endpoints owned by an agent
            if not obj:
                return False
            return obj.channel == CommsChannel.EMAIL and obj.owner_agent_id is not None

    inlines = [AgentEmailAccountInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('<path:object_id>/test-smtp/', self.admin_site.admin_view(self.test_smtp_view), name='api_endpoint_test_smtp'),
            path('<path:object_id>/test-imap/', self.admin_site.admin_view(self.test_imap_view), name='api_endpoint_test_imap'),
            path('<path:object_id>/poll-imap-now/', self.admin_site.admin_view(self.poll_imap_now_view), name='api_endpoint_poll_imap_now'),
        ]
        return custom + urls

    def owner_agent_name(self, obj):
        if obj.owner_agent:
            url = reverse("admin:api_persistentagent_change", args=[obj.owner_agent.pk])
            return format_html('<a href="{}">{}</a>', url, obj.owner_agent.name)
        return format_html('<em>External</em>')
    owner_agent_name.short_description = "Owner Agent"
    owner_agent_name.admin_order_field = 'owner_agent__name'

    def message_count(self, obj):
        sent_count = obj.messages_sent.count()
        received_count = obj.messages_received.count()
        total = sent_count + received_count
        return f"{total} ({sent_count} sent, {received_count} received)"
    message_count.short_description = "Messages"

    @admin.display(description='Test SMTP')
    def test_smtp_button(self, obj):
        if obj.channel == CommsChannel.EMAIL and obj.owner_agent_id:
            url = reverse('admin:api_endpoint_test_smtp', args=[obj.pk])
            return format_html('<a class="button" href="{}">Test SMTP</a>', url)
        return '—'

    @admin.display(description='Test IMAP')
    def test_imap_button(self, obj):
        if obj.channel == CommsChannel.EMAIL and obj.owner_agent_id:
            url = reverse('admin:api_endpoint_test_imap', args=[obj.pk])
            return format_html('<a class="button" href="{}">Test IMAP</a>', url)
        return '—'

    @admin.display(description='Poll IMAP Now')
    def poll_imap_now_button(self, obj):
        if obj.channel == CommsChannel.EMAIL and obj.owner_agent_id:
            url = reverse('admin:api_endpoint_poll_imap_now', args=[obj.pk])
            return format_html('<a class="button" href="{}">Poll Now</a>', url)
        return '—'

    def test_smtp_view(self, request, object_id):
        try:
            endpoint = PersistentAgentCommsEndpoint.objects.select_related('owner_agent').get(pk=object_id)
        except PersistentAgentCommsEndpoint.DoesNotExist:
            self.message_user(request, "Endpoint not found", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:api_persistentagentcommsendpoint_changelist"))

        if endpoint.channel != CommsChannel.EMAIL or not endpoint.owner_agent_id:
            self.message_user(request, "Test SMTP is only available for agent-owned email endpoints.", messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

        acct = getattr(endpoint, 'agentemailaccount', None)
        if not acct:
            self.message_user(request, "No Agent Email Account configured for this endpoint.", messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

        # Attempt connection
        try:
            import smtplib
            # Choose client
            if acct.smtp_security == AgentEmailAccount.SmtpSecurity.SSL:
                client = smtplib.SMTP_SSL(acct.smtp_host, int(acct.smtp_port or 465), timeout=30)
            else:
                client = smtplib.SMTP(acct.smtp_host, int(acct.smtp_port or 587), timeout=30)
            try:
                client.ehlo()
                if acct.smtp_security == AgentEmailAccount.SmtpSecurity.STARTTLS:
                    client.starttls()
                    client.ehlo()
                if acct.smtp_auth != AgentEmailAccount.AuthMode.NONE:
                    client.login(acct.smtp_username or '', acct.get_smtp_password() or '')
                # Try NOOP
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

            # Success
            from django.utils import timezone
            acct.connection_last_ok_at = timezone.now()
            acct.connection_error = ""
            acct.save(update_fields=['connection_last_ok_at', 'connection_error'])
            self.message_user(request, "SMTP connection test succeeded.", messages.SUCCESS)
            # Analytics: SMTP Test Passed
            try:
                user_id = getattr(getattr(endpoint.owner_agent, 'user', None), 'id', None)
                if user_id:
                    Analytics.track_event(
                        user_id=user_id,
                        event=AnalyticsEvent.SMTP_TEST_PASSED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'endpoint': endpoint.address,
                        },
                    )
            except Exception:
                pass
        except Exception as e:
            acct.connection_error = str(e)
            acct.save(update_fields=['connection_error'])
            self.message_user(request, f"SMTP connection test failed: {e}", messages.ERROR)
            # Analytics: SMTP Test Failed
            try:
                user_id = getattr(getattr(endpoint.owner_agent, 'user', None), 'id', None)
                if user_id:
                    Analytics.track_event(
                        user_id=user_id,
                        event=AnalyticsEvent.SMTP_TEST_FAILED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'endpoint': endpoint.address,
                            'error': str(e)[:500],
                        },
                    )
            except Exception:
                pass

        return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

    def test_imap_view(self, request, object_id):
        try:
            endpoint = PersistentAgentCommsEndpoint.objects.select_related('owner_agent').get(pk=object_id)
        except PersistentAgentCommsEndpoint.DoesNotExist:
            self.message_user(request, "Endpoint not found", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:api_persistentagentcommsendpoint_changelist"))

        if endpoint.channel != CommsChannel.EMAIL or not endpoint.owner_agent_id:
            self.message_user(request, "Test IMAP is only available for agent-owned email endpoints.", messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

        acct = getattr(endpoint, 'agentemailaccount', None)
        if not acct:
            self.message_user(request, "No Agent Email Account configured for this endpoint.", messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

        # Attempt IMAP connection
        try:
            import imaplib
            from django.utils import timezone
            if acct.imap_security == AgentEmailAccount.ImapSecurity.SSL:
                client = imaplib.IMAP4_SSL(acct.imap_host, int(acct.imap_port or 993), timeout=30)
            else:
                client = imaplib.IMAP4(acct.imap_host, int(acct.imap_port or 143), timeout=30)
                if acct.imap_security == AgentEmailAccount.ImapSecurity.STARTTLS:
                    client.starttls()
            try:
                client.login(acct.imap_username or '', acct.get_imap_password() or '')
                client.select(acct.imap_folder or 'INBOX', readonly=True)
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

            acct.connection_last_ok_at = timezone.now()
            acct.connection_error = ""
            acct.save(update_fields=['connection_last_ok_at', 'connection_error'])
            self.message_user(request, "IMAP connection test succeeded.", messages.SUCCESS)
            # Analytics: IMAP Test Passed
            try:
                user_id = getattr(getattr(endpoint.owner_agent, 'user', None), 'id', None)
                if user_id:
                    Analytics.track_event(
                        user_id=user_id,
                        event=AnalyticsEvent.IMAP_TEST_PASSED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'endpoint': endpoint.address,
                        },
                    )
            except Exception:
                pass
        except Exception as e:
            acct.connection_error = str(e)
            acct.save(update_fields=['connection_error'])
            self.message_user(request, f"IMAP connection test failed: {e}", messages.ERROR)
            # Analytics: IMAP Test Failed
            try:
                user_id = getattr(getattr(endpoint.owner_agent, 'user', None), 'id', None)
                if user_id:
                    Analytics.track_event(
                        user_id=user_id,
                        event=AnalyticsEvent.IMAP_TEST_FAILED,
                        source=AnalyticsSource.WEB,
                        properties={
                            'endpoint': endpoint.address,
                            'error': str(e)[:500],
                        },
                    )
            except Exception:
                pass

        return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

    def poll_imap_now_view(self, request, object_id):
        try:
            endpoint = PersistentAgentCommsEndpoint.objects.select_related('owner_agent').get(pk=object_id)
        except PersistentAgentCommsEndpoint.DoesNotExist:
            self.message_user(request, "Endpoint not found", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:api_persistentagentcommsendpoint_changelist"))

        acct = getattr(endpoint, 'agentemailaccount', None)
        if not acct:
            self.message_user(request, "No Agent Email Account configured for this endpoint.", messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))

        try:
            from api.agent.tasks import poll_imap_inbox
            poll_imap_inbox.delay(str(acct.pk))
            self.message_user(request, "IMAP poll enqueued.", messages.SUCCESS)
        except Exception as e:
            self.message_user(request, f"Failed to enqueue IMAP poll: {e}", messages.ERROR)

        return HttpResponseRedirect(reverse('admin:api_persistentagentcommsendpoint_change', args=[object_id]))


@admin.register(PersistentAgentMessage) 
class PersistentAgentMessageAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'owner_agent_link', 'direction_icon', 'from_address', 'to_address', 'body_summary', 'latest_status', 'conversation_link')
    list_filter = ('is_outbound', 'latest_status', 'timestamp', 'owner_agent', 'from_endpoint__channel')
    search_fields = ('body', 'from_endpoint__address', 'to_endpoint__address', 'owner_agent__name')
    readonly_fields = ('id', 'seq', 'timestamp', 'owner_agent', 'peer_agent', 'latest_sent_at')
    raw_id_fields = ('from_endpoint', 'to_endpoint', 'conversation', 'parent')
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)

    def get_queryset(self, request):
        """Optimize with select_related to prevent N+1 queries."""
        qs = super().get_queryset(request)
        return qs.select_related("owner_agent", "from_endpoint", "to_endpoint", "peer_agent")

    fieldsets = (
        ('Message Information', {
            'fields': ('id', 'seq', 'timestamp', 'is_outbound', 'body')
        }),
        ('Routing', {
            'fields': ('from_endpoint', 'to_endpoint', 'conversation', 'parent', 'owner_agent', 'peer_agent')
        }),
        ('Delivery Status', {
            'fields': ('latest_status', 'latest_sent_at', 'latest_error_message'),
            'classes': ('collapse',)
        }),
        ('Raw Data', {
            'fields': ('raw_payload',),
            'classes': ('collapse',)
        }),
    )

    def owner_agent_link(self, obj):
        if obj.owner_agent:
            url = reverse("admin:api_persistentagent_change", args=[obj.owner_agent.pk])
            return format_html('<a href="{}">{}</a>', url, obj.owner_agent.name)
        return "-"
    owner_agent_link.short_description = "Agent"
    owner_agent_link.admin_order_field = 'owner_agent__name'

    def direction_icon(self, obj):
        if obj.is_outbound:
            return format_html('<span style="color: blue; font-weight: bold;">→</span>')
        else:
            return format_html('<span style="color: green; font-weight: bold;">←</span>')
    direction_icon.short_description = "Dir"

    def from_address(self, obj):
        return obj.from_endpoint.address if obj.from_endpoint else "Unknown"
    from_address.short_description = "From"
    from_address.admin_order_field = 'from_endpoint__address'

    def to_address(self, obj):
        return obj.to_endpoint.address if obj.to_endpoint else "Conversation"
    to_address.short_description = "To"

    def body_summary(self, obj):
        if obj.body:
            clean_body = obj.body.replace('\n', ' ').strip()
            return (clean_body[:100] + '...') if len(clean_body) > 100 else clean_body
        return "-"
    body_summary.short_description = "Message"

    def conversation_link(self, obj):
        if obj.conversation:
            url = reverse("admin:api_persistentagentconversation_change", args=[obj.conversation.pk])
            return format_html('<a href="{}">View</a>', url)
        return "-"
    conversation_link.short_description = "Thread"


@admin.register(AgentPeerLink)
class AgentPeerLinkAdmin(admin.ModelAdmin):
    list_display = (
        'agents_display',
        'is_enabled',
        'quota_display',
        'feature_flag',
        'created_at',
        'updated_at',
    )
    list_filter = ('is_enabled',)
    search_fields = (
        'agent_a__name',
        'agent_a__user__email',
        'agent_b__name',
        'agent_b__user__email',
        'pair_key',
    )
    autocomplete_fields = (
        'agent_a',
        'agent_b',
        'agent_a_endpoint',
        'agent_b_endpoint',
        'created_by',
    )
    readonly_fields = (
        'pair_key',
        'created_at',
        'updated_at',
        'conversation_link',
    )
    fieldsets = (
        ('Agents', {
            'fields': ('agent_a', 'agent_b', 'created_by', 'pair_key', 'conversation_link')
        }),
        ('Quota', {
            'fields': ('messages_per_window', 'window_hours', 'is_enabled', 'feature_flag')
        }),
        ('Preferred Endpoints', {
            'fields': ('agent_a_endpoint', 'agent_b_endpoint')
        }),
    )

    @admin.display(description='Agents')
    def agents_display(self, obj):
        agent_a_name = getattr(obj.agent_a, 'name', '—')
        agent_b_name = getattr(obj.agent_b, 'name', '—')
        return format_html('{} &harr; {}', agent_a_name, agent_b_name)

    @admin.display(description='Quota')
    def quota_display(self, obj):
        return f"{obj.messages_per_window} / {obj.window_hours}h"

    @admin.display(description='Conversation')
    def conversation_link(self, obj):
        conversation = getattr(obj, 'conversation', None)
        if conversation:
            url = reverse("admin:api_persistentagentconversation_change", args=[conversation.pk])
            return format_html('<a href="{}">Open thread</a>', url)
        return "—"


@admin.register(AgentCommPeerState)
class AgentCommPeerStateAdmin(admin.ModelAdmin):
    list_display = (
        'link',
        'channel',
        'messages_per_window',
        'window_hours',
        'credits_remaining',
        'window_reset_at',
        'last_message_at',
    )
    list_filter = ('channel',)
    search_fields = (
        'link__agent_a__name',
        'link__agent_b__name',
        'link__pair_key',
    )
    autocomplete_fields = ('link',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': (
                'link',
                'channel',
                'messages_per_window',
                'window_hours',
                'credits_remaining',
                'window_reset_at',
                'last_message_at',
                'debounce_seconds',
                'created_at',
                'updated_at',
            )
        }),
    )


@admin.register(PersistentAgentConversation)
class PersistentAgentConversationAdmin(admin.ModelAdmin):
    list_display = ('display_name_or_address', 'channel', 'owner_agent_link', 'message_count', 'participant_count', 'latest_message_date')
    list_filter = ('channel', 'owner_agent')
    search_fields = ('address', 'display_name', 'owner_agent__name')
    raw_id_fields = ('owner_agent',)

    def display_name_or_address(self, obj):
        return obj.display_name if obj.display_name else obj.address
    display_name_or_address.short_description = "Conversation"
    display_name_or_address.admin_order_field = 'address'

    def owner_agent_link(self, obj):
        if obj.owner_agent:
            url = reverse("admin:api_persistentagent_change", args=[obj.owner_agent.pk])
            return format_html('<a href="{}">{}</a>', url, obj.owner_agent.name)
        return "-"
    owner_agent_link.short_description = "Agent"
    owner_agent_link.admin_order_field = 'owner_agent__name'

    def message_count(self, obj):
        count = obj.messages.count()
        if count > 0:
            url = reverse("admin:api_persistentagentmessage_changelist") + f"?conversation__id__exact={obj.pk}"
            return format_html('<a href="{}">{} messages</a>', url, count)
        return "0"
    message_count.short_description = "Messages"

    def participant_count(self, obj):
        return obj.participants.count()
    participant_count.short_description = "Participants"

    def latest_message_date(self, obj):
        latest = obj.messages.order_by('-timestamp').first()
        return latest.timestamp if latest else None
    latest_message_date.short_description = "Latest Message"
    latest_message_date.admin_order_field = 'messages__timestamp'


@admin.register(PersistentAgentStep)
class PersistentAgentStepAdmin(admin.ModelAdmin):
    list_display = ('agent_link', 'description_preview', 'credits_cost', 'task_credit_link', 'created_at', 'step_type')
    list_filter = ('agent', 'created_at')
    search_fields = ('description', 'agent__name')
    readonly_fields = ('id', 'created_at', 'credits_cost', 'task_credit')
    raw_id_fields = ('agent', 'task_credit')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('agent', 'task_credit')

    def agent_link(self, obj):
        url = reverse("admin:api_persistentagent_change", args=[obj.agent.pk])
        return format_html('<a href="{}">{}</a>', url, obj.agent.name)
    agent_link.short_description = "Agent"
    agent_link.admin_order_field = 'agent__name'

    def description_preview(self, obj):
        if obj.description:
            preview = obj.description.replace('\n', ' ').strip()
            return (preview[:150] + '...') if len(preview) > 150 else preview
        return "-"
    description_preview.short_description = "Description"

    def step_type(self, obj):
        if hasattr(obj, 'tool_call'):
            return format_html('<span style="color: blue;">Tool Call</span>')
        elif hasattr(obj, 'cron_trigger'):
            return format_html('<span style="color: green;">Cron</span>')
        elif hasattr(obj, 'system_step'):
            return format_html('<span style="color: orange;">System</span>')
        else:
            return format_html('<span style="color: gray;">General</span>')
    step_type.short_description = "Type"

    @admin.display(description='Task Credit')
    def task_credit_link(self, obj):
        if obj.task_credit_id:
            url = reverse("admin:api_taskcredit_change", args=[obj.task_credit_id])
            return format_html('<a href="{}">{}</a>', url, obj.task_credit_id)
        return "-"


@admin.register(PersistentAgentPromptArchive)
class PersistentAgentPromptArchiveAdmin(admin.ModelAdmin):
    list_display = (
        "agent_link",
        "rendered_at",
        "tokens_before",
        "tokens_after",
        "tokens_saved",
        "compressed_bytes",
        "download_link",
    )
    readonly_fields = (
        "agent",
        "rendered_at",
        "storage_key",
        "raw_bytes",
        "compressed_bytes",
        "tokens_before",
        "tokens_after",
        "tokens_saved",
        "created_at",
    )
    search_fields = ("agent__name", "agent__user__email", "storage_key")
    list_filter = ("agent",)
    date_hierarchy = "rendered_at"
    ordering = ("-rendered_at",)
    list_select_related = ("agent",)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<uuid:pk>/download/",
                self.admin_site.admin_view(self.download_view),
                name="api_persistentagentpromptarchive_download",
            ),
        ]
        return custom_urls + urls

    def agent_link(self, obj):
        url = reverse("admin:api_persistentagent_change", args=[obj.agent.pk])
        return format_html('<a href="{}">{}</a>', url, obj.agent.name)
    agent_link.short_description = "Agent"
    agent_link.admin_order_field = "agent__name"

    def download_link(self, obj):
        url = reverse("admin:api_persistentagentpromptarchive_download", args=[obj.pk])
        return format_html('<a href="{}">Download</a>', url)
    download_link.short_description = "Prompt"

    def download_view(self, request, pk, *args, **kwargs):
        archive = self.get_object(request, pk)
        changelist_url = reverse("admin:api_persistentagentpromptarchive_changelist")
        if not archive:
            self.message_user(request, "Prompt archive not found.", level=messages.ERROR)
            return HttpResponseRedirect(changelist_url)
        if not default_storage.exists(archive.storage_key):
            self.message_user(
                request,
                "Archived prompt payload is missing from storage.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        filename = archive.storage_key.rsplit("/", 1)[-1] or f"{archive.pk}.json.zst"
        if filename.endswith(".zst"):
            download_name = filename[:-4]
        else:
            download_name = filename
        if "." not in download_name:
            download_name += ".json"

        def content_stream():
            with default_storage.open(archive.storage_key, "rb") as stored:
                dctx = zstd.ZstdDecompressor()
                with dctx.stream_reader(stored) as reader:
                    while True:
                        chunk = reader.read(65536)
                        if not chunk:
                            break
                        yield chunk

        response = StreamingHttpResponse(content_stream(), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{download_name}"'
        return response

@admin.register(UserBilling)
class UserBillingAdmin(admin.ModelAdmin):
    list_display = ['id', 'user_id', 'user', 'subscription', 'max_extra_tasks', 'billing_cycle_anchor']
    list_filter = ['subscription', 'user_id']
    search_fields = ['id', 'subscription', 'user__email', 'user__username']
    readonly_fields = ['id', 'user']
    actions = [
        'align_anchor_from_stripe',
    ]

    @admin.action(description="Align anchor day with Stripe period start")
    def align_anchor_from_stripe(self, request, queryset):
        """Admin action: for selected UserBilling rows, set billing_cycle_anchor
        to the user's Stripe subscription current_period_start.day (when available).

        Skips rows without an active Stripe subscription.
        """
        from util.subscription_helper import get_active_subscription

        updated = 0
        skipped = 0
        errors = 0
        for ub in queryset.select_related('user'):
            try:
                sub = get_active_subscription(ub.user)
                if not sub or not getattr(sub.stripe_data, 'current_period_start', None):
                    skipped += 1
                    continue
                new_day = sub.stripe_data['current_period_start'].day
                if ub.billing_cycle_anchor != new_day:
                    ub.billing_cycle_anchor = new_day
                    ub.save(update_fields=["billing_cycle_anchor"])
                    updated += 1
                else:
                    skipped += 1
            except Exception as e:
                logging.error("Failed to align billing anchor for user %s: %s", ub.user.id, e)
                errors += 1

        self.message_user(
            request,
            f"Anchor alignment complete: updated={updated}, skipped={skipped}, errors={errors}",
            level=messages.INFO,
        )


@admin.register(OrganizationBilling)
class OrganizationBillingAdmin(admin.ModelAdmin):
    list_select_related = ('organization',)
    list_display = [
        'id',
        'organization_id',
        'organization',
        'subscription',
        'billing_cycle_anchor',
        'stripe_customer_id',
        'stripe_subscription_id',
        'cancel_at',
        'cancel_at_period_end',
    ]
    list_filter = ['subscription', 'cancel_at_period_end']
    search_fields = ['id', 'organization__name', 'organization__id', 'stripe_customer_id', 'stripe_subscription_id']
    readonly_fields = ['id', 'organization', 'created_at', 'updated_at']


@admin.action(description="Sync numbers from Twilio")
def sync_from_twilio(modeladmin, request, queryset):
    sync_twilio_numbers.delay()
    messages.success(request, "Background sync started – this may take a minute. Refresh the page to see updates.")

@admin.register(SmsNumber)
class SmsNumberAdmin(admin.ModelAdmin):
    change_form_template = "admin/smsnumber_change_form.html"
    change_list_template = "admin/smsnumber_change_list.html"
    actions = [sync_from_twilio]
    list_display = ('friendly_number', 'provider', 'is_active', 'in_use', 'country', 'created_at')
    list_filter = ('provider', 'is_active', 'created_at')
    search_fields = ('phone_number', 'provider__name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'provider', 'is_sms_enabled', 'is_mms_enabled', 'messaging_service_sid', 'extra')

    @admin.display(description="Phone", ordering="text")
    def friendly_number(self, obj):
        """Render +14155551234 → (415) 555-1234 or +1 415 555 1234."""
        import phonenumbers
        from phonenumbers import PhoneNumberFormat

        try:
            parsed = phonenumbers.parse(obj.phone_number, None)  # E.164 in, region auto-detected
            pretty = phonenumbers.format_number(
                parsed, PhoneNumberFormat.NATIONAL  # or INTERNATIONAL
            )

            return pretty
        except phonenumbers.NumberParseException:
            return  obj.phone_number # fall back if the data is malformed

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        in_use_subquery = PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.SMS,
            address__iexact=OuterRef('phone_number')
        )
        return qs.annotate(is_in_use=Exists(in_use_subquery))

    @admin.display(description="In Use", boolean=True, ordering="is_in_use")
    def in_use(self, obj):
        """Return True if any agent SMS endpoint uses this number."""
        return obj.is_in_use

    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'phone_number', 'is_active')
        }),
        ('SMS/MMS Configuration', {
            'fields': ('is_sms_enabled', 'is_mms_enabled', 'messaging_service_sid', 'provider', 'extra')
        }),
        ('Location Information', {
            'fields': ('country', 'region')
        }),
        ('Metadata', {
            'fields': ('sid', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def changelist_view(self, request, extra_context=None):
        """Inject counts of numbers in use for the change list template."""
        if extra_context is None:
            extra_context = {}

        in_use_numbers_qs = PersistentAgentCommsEndpoint.objects.filter(
            channel=CommsChannel.SMS,
        ).values("address")

        extra_context["in_use_count"] = SmsNumber.objects.filter(
            phone_number__in=in_use_numbers_qs,
        ).count()

        extra_context["total_count"] = SmsNumber.objects.count()

        return super().changelist_view(request, extra_context=extra_context)



    def get_urls(self):
        urls = super().get_urls()
        extra = [
            path(
                "<path:object_id>/test/",
                self.admin_site.admin_view(self.test_sms_view),
                name="smsnumber_test_sms",
            ),
            path(
                "sync/",  # /admin/api/smsnumber/sync/
                self.admin_site.admin_view(self.sync_view),
                name="smsnumber_sync",
            )
        ]
        return extra + urls

    # ③ view that queues the task
    def sync_view(self, request):
        if not request.user.has_perm("api.change_smsnumber"):
            messages.error(request, "Permission denied.")
            return HttpResponseRedirect(reverse("admin:api_smsnumber_changelist"))

        sync_twilio_numbers.delay()
        messages.success(request, "Background sync started – refresh in a minute.")
        return HttpResponseRedirect(reverse("admin:api_smsnumber_changelist"))

    # 𝟑𝗮  view
    def test_sms_view(self, request, object_id):
        sms_number = self.get_object(request, object_id)

        if request.method == "POST":
            form = TestSmsForm(request.POST)
            if form.is_valid():
                send_test_sms.delay(
                    sms_number.id,
                    form.cleaned_data["to"],
                    form.cleaned_data["body"],
                )
                messages.success(request, "Test SMS queued – check your phone!")
                return HttpResponseRedirect(
                    reverse("admin:api_smsnumber_change", args=[object_id])
                )
        else:
            form = TestSmsForm()

        context = dict(
            self.admin_site.each_context(request),
            opts=self.model._meta,
            form=form,
            title=f"Send test SMS from {sms_number.phone_number}",
            original=sms_number,
        )

        return TemplateResponse(request, "admin/test_sms_form.html", context)


@admin.register(LinkShortener)
class LinkShortenerAdmin(admin.ModelAdmin):
    list_display = ("code", "shortened", "url", "hits", "created_at")
    readonly_fields = ("hits", "shortened", "created_at", "updated_at")

    @admin.display(description="Short URL")
    def shortened(self, obj):
        try:
            return obj.get_absolute_url()
        except Exception:
            return f"/{obj.code}"


# --------------------------------------------------------------------------- #
#  LLM Provider + Endpoint Admin (DB-configurable LLM routing)
# --------------------------------------------------------------------------- #
from .models import (
    LLMProvider,
    PersistentModelEndpoint,
    PersistentTokenRange,
    PersistentLLMTier,
    PersistentTierEndpoint,
    BrowserModelEndpoint,
    BrowserLLMPolicy,
    BrowserLLMTier,
    BrowserTierEndpoint,
)


from .admin_forms import LLMProviderForm


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    form = LLMProviderForm
    list_display = ("display_name", "key", "enabled", "_key_source", "browser_backend")
    list_filter = ("enabled", "browser_backend")
    search_fields = ("display_name", "key", "env_var_name")
    readonly_fields = ("_key_source",)

    def get_readonly_fields(self, request, obj=None):
        # Only show Key Source after the object exists
        if obj is None:
            return tuple()
        return super().get_readonly_fields(request, obj)

    def get_fieldsets(self, request, obj=None):
        base = [
            (None, {"fields": ("display_name", "key", "enabled")}),
            ("Credentials", {"fields": ("api_key", "clear_api_key", "env_var_name")}),
            ("Provider Options", {"fields": ("browser_backend", "supports_safety_identifier")}),
            ("Vertex (Google)", {"fields": ("vertex_project", "vertex_location")}),
        ]
        if obj is not None:
            # Append Key Source in credentials when editing existing provider
            base[1][1]["fields"] = ("api_key", "clear_api_key", "env_var_name", "_key_source")
        return base

    def _key_source(self, obj):
        if obj.api_key_encrypted:
            return "Admin"
        if obj.env_var_name:
            import os
            return "Env" if os.getenv(obj.env_var_name) else "Missing"
        return "Missing"
    _key_source.short_description = "Key Source"


@admin.register(PersistentModelEndpoint)
class PersistentModelEndpointAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "provider",
        "litellm_model",
        "api_base",
        "enabled",
        "supports_tool_choice",
        "use_parallel_tool_calls",
        "supports_vision",
    )
    list_filter = ("enabled", "provider", "supports_vision")
    search_fields = ("key", "litellm_model")
    fields = (
        "key",
        "provider",
        "enabled",
        "litellm_model",
        "api_base",
        "temperature_override",
        "supports_tool_choice",
        "use_parallel_tool_calls",
        "supports_vision",
    )


class PersistentTierEndpointInline(admin.TabularInline):
    model = PersistentTierEndpoint
    extra = 0


@admin.register(PersistentLLMTier)
class PersistentLLMTierAdmin(admin.ModelAdmin):
    list_display = ("token_range", "order", "description")
    list_filter = ("token_range",)
    inlines = [PersistentTierEndpointInline]


@admin.register(PersistentTokenRange)
class PersistentTokenRangeAdmin(admin.ModelAdmin):
    list_display = ("name", "min_tokens", "max_tokens")
    ordering = ("min_tokens",)


@admin.register(BrowserModelEndpoint)
class BrowserModelEndpointAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "provider",
        "browser_model",
        "browser_base_url",
        "max_output_tokens",
        "enabled",
        "supports_vision",
    )
    list_filter = ("enabled", "provider", "supports_vision")
    search_fields = ("key", "browser_model", "browser_base_url")
    fields = (
        "key",
        "provider",
        "enabled",
        "browser_model",
        "browser_base_url",
        "max_output_tokens",
        "supports_vision",
    )


class BrowserTierEndpointInline(admin.TabularInline):
    model = BrowserTierEndpoint
    extra = 0


@admin.register(BrowserLLMTier)
class BrowserLLMTierAdmin(admin.ModelAdmin):
    list_display = ("policy", "order", "description")
    list_filter = ("policy",)
    inlines = [BrowserTierEndpointInline]


@admin.register(BrowserLLMPolicy)
class BrowserLLMPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)
    search_fields = ("code", "url")

    @admin.display(description="Short URL")
    def shortened(self, obj):
        """Generate the URL for the landing page."""
        if not obj.pk or not obj.code:
            return "—"

        rel =  reverse('short_link', kwargs={'code': obj.code})
        current_site = Site.objects.get_current()

        # get if https from request
        protocol = 'https://'

        # Ensure the site domain is used to create the absolute URL
        absolute_url = f"{protocol}{current_site.domain}{rel}"

        return format_html(f'<a href="{absolute_url}" target="_blank">{absolute_url}</a>')


# ------------------------------------------------------------------
# Attachments & Filespaces (Admin)
# ------------------------------------------------------------------

@admin.register(PersistentAgentMessageAttachment)
class PersistentAgentMessageAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        'filename',
        'file_size',
        'content_type',
        'content_present',
        'filespace_node_link',
        'owner_agent_link',
        'message_timestamp',
        'download_link',
    )
    list_filter = (
        'content_type',
        'message__from_endpoint__channel',
        'message__owner_agent',
    )
    search_fields = (
        'filename',
        'message__body',
        'message__from_endpoint__address',
        'message__to_endpoint__address',
        'message__conversation__address',
        'message__owner_agent__name',
    )
    raw_id_fields = ('message', 'filespace_node')
    ordering = ('-message__timestamp',)

    @admin.display(description='Content Present', boolean=True)
    def content_present(self, obj):
        try:
            return bool(obj.file and getattr(obj.file, 'name', None) and obj.file.storage.exists(obj.file.name))
        except Exception:
            # If storage check fails (network, etc.), fall back to whether a name exists
            return bool(obj.file and getattr(obj.file, 'name', None))

    @admin.display(description='Agent')
    def owner_agent_link(self, obj):
        agent = getattr(obj.message, 'owner_agent', None)
        if agent:
            url = reverse("admin:api_persistentagent_change", args=[agent.pk])
            return format_html('<a href="{}">{}</a>', url, agent.name)
        return '—'

    @admin.display(description='Timestamp', ordering='message__timestamp')
    def message_timestamp(self, obj):
        return obj.message.timestamp if obj.message else None

    @admin.display(description='Download')
    def download_link(self, obj):
        try:
            if obj.file and getattr(obj.file, 'url', None):
                return format_html('<a href="{}" target="_blank">Download</a>', obj.file.url)
        except Exception:
            pass
        return '—'

    @admin.display(description='Filespace Node')
    def filespace_node_link(self, obj):
        node = getattr(obj, 'filespace_node', None)
        if not node:
            return '—'
        try:
            url = reverse("admin:api_agentfsnode_change", args=[node.pk])
            label = getattr(node, 'path', None) or str(node.pk)
            return format_html('<a href="{}">{}</a>', url, label)
        except Exception:
            return str(node.pk)


@admin.register(AgentFileSpace)
class AgentFileSpaceAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'owner_user',
        'agent_count',
        'node_count',
        'created_at',
        'browse_nodes_link',
    )
    search_fields = ('name', 'owner_user__email', 'id')
    list_filter = ('owner_user',)
    readonly_fields = ('id', 'created_at', 'updated_at')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Annotate counts without heavy joins in list_display
        return qs.annotate(_agent_count=Count('access'), _node_count=Count('nodes'))

    @admin.display(description='Agents', ordering='_agent_count')
    def agent_count(self, obj):
        return getattr(obj, '_agent_count', None) or obj.access.count()

    @admin.display(description='Nodes', ordering='_node_count')
    def node_count(self, obj):
        return getattr(obj, '_node_count', None) or obj.nodes.count()

    @admin.display(description='Browse')
    def browse_nodes_link(self, obj):
        url = reverse('admin:api_agentfsnode_changelist') + f'?filespace__id__exact={obj.pk}'
        return format_html('<a href="{}">Open Nodes</a>', url)


@admin.register(AgentFileSpaceAccess)
class AgentFileSpaceAccessAdmin(admin.ModelAdmin):
    list_display = ('filespace', 'agent', 'role', 'is_default', 'granted_at')
    list_filter = ('role', 'is_default', 'filespace', 'agent')
    search_fields = ('filespace__name', 'agent__name')
    raw_id_fields = ('filespace', 'agent')
    ordering = ('-granted_at',)


@admin.register(AgentFsNode)
class AgentFsNodeAdmin(admin.ModelAdmin):
    list_display = (
        'filespace',
        'path',
        'node_type',
        'size_bytes',
        'mime_type',
        'is_deleted',
        'created_at',
        'download_link',
    )
    list_filter = (
        'filespace',
        'node_type',
        'is_deleted',
        'mime_type',
    )
    search_fields = ('path', 'name', 'mime_type')
    raw_id_fields = ('filespace', 'parent', 'created_by_agent')
    readonly_fields = ('id', 'created_at', 'updated_at', 'path')
    date_hierarchy = 'created_at'
    ordering = ('filespace', 'path')

    @admin.display(description='Download')
    def download_link(self, obj):
        if obj.node_type != AgentFsNode.NodeType.FILE:
            return '—'
        try:
            if obj.pk:
                url = reverse('admin:api_agentfsnode_download', args=[obj.pk])
                return format_html('<a href="{}">Download</a>', url)
        except Exception:
            pass
        return '—'

    # Provide an authenticated download endpoint that streams from storage
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<path:object_id>/download/',
                self.admin_site.admin_view(self.download_view),
                name='api_agentfsnode_download',
            )
        ]
        return custom + urls

    def download_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if not obj:
            self.message_user(request, 'File not found', messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_agentfsnode_changelist'))

        if obj.node_type != AgentFsNode.NodeType.FILE:
            self.message_user(request, 'This node is not a file', messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_agentfsnode_change', args=[object_id]))

        if not obj.content or not getattr(obj.content, 'name', None):
            self.message_user(request, 'No content associated with this file node', messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_agentfsnode_change', args=[object_id]))

        try:
            storage = obj.content.storage
            name = obj.content.name
            if hasattr(storage, 'exists') and not storage.exists(name):
                self.message_user(request, 'Stored blob is missing or has been moved', messages.ERROR)
                return HttpResponseRedirect(reverse('admin:api_agentfsnode_change', args=[object_id]))

            fh = storage.open(name, 'rb')
            filename = obj.name or 'file'
            content_type = obj.mime_type or 'application/octet-stream'
            response = FileResponse(fh, as_attachment=True, filename=filename, content_type=content_type)
            return response
        except Exception as e:
            self.message_user(request, f'Error streaming file: {e}', messages.ERROR)
            return HttpResponseRedirect(reverse('admin:api_agentfsnode_change', args=[object_id]))


@admin.register(PersistentAgentTemplate)
class PersistentAgentTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'display_name', 'category', 'recommended_contact_channel', 'base_schedule',
        'schedule_jitter_minutes', 'priority', 'is_active', 'updated_at'
    )
    list_filter = ('category', 'recommended_contact_channel', 'is_active')
    search_fields = ('display_name', 'tagline', 'description', 'code')
    ordering = ('priority', 'display_name')
    readonly_fields = ('created_at', 'updated_at')
    prepopulated_fields = {"code": ("display_name",)}
    fieldsets = (
        ('Identity', {
            'fields': ('code', 'display_name', 'tagline', 'category', 'priority', 'is_active')
        }),
        ('Narrative', {
            'fields': ('description', 'charter')
        }),
        ('Cadence & Triggers', {
            'fields': ('base_schedule', 'schedule_jitter_minutes', 'event_triggers')
        }),
        ('Tools & Communication', {
            'fields': ('default_tools', 'recommended_contact_channel', 'hero_image_path')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(ToolFriendlyName)
class ToolFriendlyNameAdmin(admin.ModelAdmin):
    list_display = ('tool_name', 'display_name', 'updated_at')
    search_fields = ('tool_name', 'display_name')
    ordering = ('tool_name',)
    readonly_fields = ('created_at', 'updated_at')
